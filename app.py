from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from bridge_generator import sync_sidecar_logic_only, write_bridge_bundle
from bridge_service import (
    build_preview_from_source,
    normalize_repo_source,
    resolve_local_project_root_from_repo_source,
)
from planner import generate_steps_from_repo
from schemas import PlanRequest, PlanResponse, UIModificationContext
from state import graph
from workplace import ensure_thread_workplace, ensure_workplace_root, generate_thread_id, workplace_config

load_dotenv()


def _ensure_data_dir() -> str:
    data_dir = os.path.join(os.path.dirname(__file__), ".data")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


app = FastAPI(title="AI-Native Stateful UI - MVP Plan API")


def _cors_origins() -> List[str]:
    """
    Local default:
    - http://localhost:3001
    - http://127.0.0.1:3001

    Optional override:
    - CORS_ALLOW_ORIGINS="http://localhost:3001,http://127.0.0.1:3001"
    - CORS_ALLOW_ORIGINS="*"  (dev only)
    """
    raw = (os.getenv("CORS_ALLOW_ORIGINS") or "").strip()
    if not raw:
        return ["http://localhost:3001", "http://127.0.0.1:3001"]
    if raw == "*":
        return ["*"]
    return [x.strip() for x in raw.split(",") if x.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _raise_upstream_friendly_error(e: Exception, *, action: str) -> None:
    """
    Convert upstream errors into consistent API responses.
    """
    if isinstance(e, HTTPException):
        raise e
    if isinstance(e, ValueError):
        raise HTTPException(status_code=400, detail=str(e))
    raise HTTPException(status_code=500, detail=f"{action} failed: {e}")


def _checkpoint_id_from_config(config: Dict[str, Any]) -> Optional[str]:
    configurable = config.get("configurable") or {}
    return configurable.get("checkpoint_id") or config.get("checkpoint_id")


def _step_summary_from_values(values: Dict[str, Any], last_step_id: Optional[str]) -> Optional[str]:
    if not last_step_id:
        return None
    step_outputs = values.get("step_outputs") or {}
    if not isinstance(step_outputs, dict):
        return None
    step_out = step_outputs.get(last_step_id) or {}
    if not isinstance(step_out, dict):
        return None
    summary = step_out.get("checkpoint_summary")
    return summary if isinstance(summary, str) else None


def _build_anchor_items(snapshots: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in snapshots:
        cfg = getattr(s, "config", {}) or {}
        values = getattr(s, "values", {}) or {}
        last_step_id = values.get("last_step_id")
        out.append(
            {
                "checkpoint_id": _checkpoint_id_from_config(cfg),
                "current_step_index": values.get("current_step_index"),
                "last_step_id": last_step_id,
                "checkpoint_summary": _step_summary_from_values(values, last_step_id),
                "replan_required": values.get("replan_required", False),
            }
        )
    return out


def _normalize_context_ui(raw: Optional[Dict[str, Any]], user_intent: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None

    normalized = {
        "target_id": raw.get("target_id") or raw.get("componentKey") or raw.get("id") or "unknown_target",
        "component_type": raw.get("component_type") or raw.get("componentType") or "unknown_component",
        "original_spec": raw.get("original_spec") or raw.get("componentJson") or {},
        "user_intent": user_intent,
        "scope": raw.get("scope") or "content",
    }
    try:
        if hasattr(UIModificationContext, "model_validate"):
            return UIModificationContext.model_validate(normalized).model_dump()
        return UIModificationContext.parse_obj(normalized).dict()
    except Exception:
        return normalized


def _stream_until_steps(
    *,
    stream_input: Optional[Dict[str, Any]],
    config: Dict[str, Any],
    max_steps: int,
    initial_last_step_id: Optional[str],
) -> int:
    steps_completed = 0
    last_seen_step_id = initial_last_step_id
    for ev in graph.stream(stream_input, config, stream_mode="values"):
        last_step_id = ev.get("last_step_id")
        if not last_step_id or last_step_id == last_seen_step_id:
            continue
        steps_completed += 1
        last_seen_step_id = last_step_id
        if steps_completed >= max_steps:
            break
    return steps_completed


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_dict(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_timeline_payload(thread_id: str) -> Optional[Dict[str, Any]]:
    try:
        wp = ensure_thread_workplace(thread_id)
    except Exception:
        return None
    timeline_path = Path(wp["ui_dir"]) / "timeline.json"
    payload = _read_json_dict(timeline_path)
    return payload or None


def _values_from_timeline_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for key in (
        "repo_url",
        "user_notes",
        "plan",
        "current_step_index",
        "last_step_id",
        "step_outputs",
        "replan_required",
        "context_ui",
        "image_url",
    ):
        if key in payload:
            values[key] = payload.get(key)

    if not isinstance(values.get("plan"), list):
        values["plan"] = []
    if not isinstance(values.get("step_outputs"), dict):
        values["step_outputs"] = {}
    if not isinstance(values.get("current_step_index"), int):
        values["current_step_index"] = 0
    return values


def _load_thread_values(thread_id: str) -> Tuple[Dict[str, Any], Optional[str], str]:
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    values = snapshot.values or {}
    if values:
        checkpoint_id = _checkpoint_id_from_config(snapshot.config or {})
        return values, checkpoint_id, "graph"

    timeline = _load_timeline_payload(thread_id)
    if timeline:
        timeline_values = _values_from_timeline_payload(timeline)
        if timeline_values:
            raw_cp = timeline.get("checkpoint_id")
            checkpoint_id = raw_cp if isinstance(raw_cp, str) and raw_cp else None
            return timeline_values, checkpoint_id, "timeline"

    raise HTTPException(status_code=404, detail=f"Thread not found or has no state: {thread_id}")


def _build_timeline_anchor_items(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    plan = values.get("plan") if isinstance(values.get("plan"), list) else []
    step_outputs = values.get("step_outputs") if isinstance(values.get("step_outputs"), dict) else {}
    if not step_outputs:
        return []

    step_index_by_id: Dict[str, int] = {}
    for idx, step in enumerate(plan):
        if not isinstance(step, dict):
            continue
        sid = str(step.get("id") or f"step_{idx + 1}")
        step_index_by_id[sid] = idx

    ordered_step_ids: List[str] = []
    for step in plan:
        if not isinstance(step, dict):
            continue
        sid = str(step.get("id") or "")
        if sid and sid in step_outputs and sid not in ordered_step_ids:
            ordered_step_ids.append(sid)
    for sid in step_outputs.keys():
        sid_text = str(sid)
        if sid_text and sid_text not in ordered_step_ids:
            ordered_step_ids.append(sid_text)

    out: List[Dict[str, Any]] = []
    replan_required = bool(values.get("replan_required", False))
    for idx, sid in enumerate(ordered_step_ids):
        raw_out = step_outputs.get(sid)
        step_out = raw_out if isinstance(raw_out, dict) else {}
        summary_raw = step_out.get("checkpoint_summary")
        summary = summary_raw if isinstance(summary_raw, str) else None
        out.append(
            {
                "checkpoint_id": None,
                "current_step_index": step_index_by_id.get(sid, idx),
                "last_step_id": sid,
                "checkpoint_summary": summary,
                "replan_required": replan_required,
            }
        )
    return out


def _safe_step_file_name(step_id: str) -> str:
    allowed = []
    for ch in (step_id or "unknown_step"):
        if ch.isalnum() or ch in {"_", "-", "."}:
            allowed.append(ch)
        else:
            allowed.append("_")
    return "".join(allowed) or "unknown_step"


def _is_thin_fallback_ui(ui_render: Any) -> bool:
    if not isinstance(ui_render, dict):
        return True
    if str(ui_render.get("type") or "").strip() != "info_card":
        return False
    content = ui_render.get("content")
    if not isinstance(content, dict):
        return True
    text = str(content.get("text") or "")
    return ("未提供 ui_render 细节" in text) or ("No detailed ui_render provided" in text)


def _build_richer_latest_ui(values: Dict[str, Any], last_step_id: Optional[str]) -> Dict[str, Any]:
    plan = values.get("plan") if isinstance(values.get("plan"), list) else []
    step_outputs = values.get("step_outputs") if isinstance(values.get("step_outputs"), dict) else {}
    current_step_index = values.get("current_step_index") if isinstance(values.get("current_step_index"), int) else 0
    total_steps = len(plan)
    completed = len(step_outputs)

    next_label = "N/A"
    if 0 <= current_step_index < total_steps:
        nxt = plan[current_step_index]
        if isinstance(nxt, dict):
            next_label = str(nxt.get("id") or "N/A")

    lines: List[str] = []
    for sid, out in step_outputs.items():
        if not isinstance(out, dict):
            continue
        summary = str(out.get("checkpoint_summary") or "").strip()
        if summary:
            lines.append(f"{sid}: {summary}")
    lines = lines[-8:]

    if lines:
        return {
            "type": "terminal",
            "content": {
                "title": "Execution Snapshot",
                "lines": [
                    f"Completed steps: {completed}/{total_steps}",
                    f"Last step: {last_step_id or '-'}",
                    f"Next step: {next_label}",
                    *lines,
                ],
            },
        }

    return {
        "type": "form",
        "content": {
            "title": "Continue Workflow",
            "fields": [
                {
                    "name": "refinement_prompt",
                    "label": "Refinement Prompt",
                    "kind": "textarea",
                    "placeholder": "Optional prompt for rollback/regeneration.",
                },
                {
                    "name": "target_project_root",
                    "label": "Target Project Root",
                    "kind": "text",
                    "placeholder": "e.g. D:\\my-project",
                },
            ],
        },
    }


def _persist_ui_artifacts(
    thread_id: str,
    values: Dict[str, Any],
    *,
    checkpoint_id: Optional[str] = None,
) -> Dict[str, str]:
    wp = ensure_thread_workplace(thread_id)
    ui_dir = Path(wp["ui_dir"])
    steps_dir = ui_dir / "steps"

    plan = values.get("plan") or []
    step_outputs = values.get("step_outputs") or {}
    last_step_id = values.get("last_step_id")

    latest_ui = None
    if last_step_id and isinstance(step_outputs, dict):
        last_step_output = step_outputs.get(last_step_id) or {}
        if isinstance(last_step_output, dict):
            latest_ui = last_step_output.get("ui_render")

    if not latest_ui or _is_thin_fallback_ui(latest_ui):
        latest_ui = _build_richer_latest_ui(values, last_step_id)

    timeline_payload = {
        "thread_id": thread_id,
        "checkpoint_id": checkpoint_id,
        "repo_url": values.get("repo_url"),
        "user_notes": values.get("user_notes"),
        "current_step_index": values.get("current_step_index"),
        "last_step_id": last_step_id,
        "plan": plan if isinstance(plan, list) else [],
        "step_outputs": step_outputs if isinstance(step_outputs, dict) else {},
        "replan_required": values.get("replan_required", False),
        "context_ui": values.get("context_ui"),
        "image_url": values.get("image_url"),
    }

    latest_ui_path = ui_dir / "latest_ui.json"
    timeline_path = ui_dir / "timeline.json"
    _write_json(latest_ui_path, latest_ui)
    _write_json(timeline_path, timeline_payload)

    if isinstance(step_outputs, dict):
        for step_id, out in step_outputs.items():
            if not isinstance(out, dict):
                continue
            step_path = steps_dir / f"{_safe_step_file_name(str(step_id))}.json"
            _write_json(step_path, out)

    return {
        "ui_dir": str(ui_dir),
        "latest_ui_path": str(latest_ui_path),
        "timeline_path": str(timeline_path),
    }


def _resolve_target_dir(raw_target_dir: str) -> Path:
    target = (raw_target_dir or "").strip()
    if not target:
        raise ValueError("target_dir cannot be empty.")
    p = Path(target).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()
    return p


def _to_posix_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_project_integration_files(
    *,
    thread_id: str,
    project_root: Path,
    integration_ui_dir: Path,
    mode: Literal["copy", "sync"],
) -> Dict[str, str]:
    integration_root = project_root / ".aui-dashboard"
    manifest_path = integration_root / "manifest.json"
    readme_path = integration_root / "README_IMPORT.md"
    entry_file_path = project_root / "AUI_UI_ENTRY.json"

    now = datetime.now(timezone.utc).isoformat()
    rel_ui_dir = _to_posix_rel(integration_ui_dir, project_root)
    rel_latest_ui = _to_posix_rel(integration_ui_dir / "latest_ui.json", project_root)
    rel_timeline = _to_posix_rel(integration_ui_dir / "timeline.json", project_root)
    rel_steps_dir = _to_posix_rel(integration_ui_dir / "steps", project_root)

    manifest: Dict[str, Any] = {}
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except Exception:
            manifest = {}

    exports = manifest.get("exports")
    if not isinstance(exports, dict):
        exports = {}

    exports[thread_id] = {
        "thread_id": thread_id,
        "mode": mode,
        "ui_dir": rel_ui_dir,
        "latest_ui_path": rel_latest_ui,
        "timeline_path": rel_timeline,
        "steps_dir": rel_steps_dir,
        "exported_at": now,
    }

    manifest.update(
        {
            "version": 1,
            "current_thread_id": thread_id,
            "updated_at": now,
            "exports": exports,
        }
    )

    entry_payload = {
        "version": 1,
        "thread_id": thread_id,
        "generated_at": now,
        "ui_dir": rel_ui_dir,
        "latest_ui_path": rel_latest_ui,
        "timeline_path": rel_timeline,
        "steps_dir": rel_steps_dir,
        "manifest_path": _to_posix_rel(manifest_path, project_root),
    }

    readme_content = f"""# AUI Dashboard UI Integration

This project root has been connected to AUI Dashboard exports.

- Current thread: `{thread_id}`
- UI directory: `{rel_ui_dir}`
- Latest UI file: `{rel_latest_ui}`
- Timeline file: `{rel_timeline}`
- Steps directory: `{rel_steps_dir}`
- Entry file: `AUI_UI_ENTRY.json`

Integration tips:
1. `AUI_UI_ENTRY.json` is a static integration entry (for embedding/rendering exported UI files).
2. Render `latest_ui.json` as the current snapshot UI.
3. Use `timeline.json` and `steps/*.json` for anchor history visualization.
4. For runtime control (start/stop/log/rollback), run `python ./.aui-dashboard/ui/ui_runner.py`.

Notes:
- `mode=sync` only syncs `{rel_ui_dir}` and does not delete project source files outside this folder.
"""

    _write_json(manifest_path, manifest)
    _write_json(entry_file_path, entry_payload)
    _write_text(readme_path, readme_content)

    return {
        "target_project_root": str(project_root),
        "integration_ui_dir": str(integration_ui_dir),
        "manifest_path": str(manifest_path),
        "entry_file_path": str(entry_file_path),
        "readme_path": str(readme_path),
    }


def _sync_directory(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)

    # Remove files/dirs that no longer exist in source.
    existing = sorted(target.rglob("*"), key=lambda x: len(x.parts), reverse=True)
    for path in existing:
        rel = path.relative_to(target)
        source_peer = source / rel
        if source_peer.exists():
            continue
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

    shutil.copytree(source, target, dirs_exist_ok=True)


def _list_ui_files(ui_dir: Path, max_items: int = 500) -> List[str]:
    if not ui_dir.exists():
        return []
    files: List[str] = []
    for p in sorted(ui_dir.rglob("*")):
        if p.is_file():
            files.append(str(p.relative_to(ui_dir)))
        if len(files) >= max_items:
            break
    return files


@app.on_event("startup")
def _startup_init_dirs() -> None:
    _ensure_data_dir()
    ensure_workplace_root()


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}


@app.get("/workplace")
def get_workplace_config() -> Dict[str, str]:
    return workplace_config()


@app.post("/plan", response_model=PlanResponse)
def plan(req: PlanRequest) -> PlanResponse:
    thread_id = req.thread_id or f"thread_{uuid.uuid4().hex}"
    try:
        ensure_thread_workplace(thread_id)
        plan_obj = generate_steps_from_repo(req)
    except Exception as e:
        _raise_upstream_friendly_error(e, action="plan")

    data_dir = _ensure_data_dir()
    thread_dir = os.path.join(data_dir, thread_id)
    os.makedirs(thread_dir, exist_ok=True)
    steps_path = os.path.join(thread_dir, "steps.json")

    with open(steps_path, "w", encoding="utf-8") as f:
        if hasattr(plan_obj, "model_dump_json"):
            f.write(plan_obj.model_dump_json(ensure_ascii=False, indent=2))
        else:
            f.write(json.dumps(plan_obj.dict(), ensure_ascii=False, indent=2))

    return PlanResponse(thread_id=thread_id, plan=plan_obj, steps_json_path=steps_path)


class RollbackRequest(BaseModel):
    thread_id: str
    checkpoint_id: Optional[str] = None
    step_id: Optional[str] = None
    user_notes: str
    context_ui: Optional[Dict[str, Any]] = None
    image_url: Optional[str] = None
    max_steps: int = Field(1, ge=1, le=200)


class RunRequest(BaseModel):
    repo_url: str
    user_notes: str = ""
    thread_id: Optional[str] = None
    thread_name: Optional[str] = None
    max_steps: int = Field(1, ge=1, le=200)


class ExportUIRequest(BaseModel):
    target_dir: Optional[str] = None
    target_project_root: Optional[str] = None
    mode: Literal["copy", "sync"] = "copy"


class BridgePreviewRequest(BaseModel):
    repo_url: Optional[str] = None
    repo_source: Optional[str] = None
    user_notes: str = ""
    thread_id: Optional[str] = None
    override_launch_command: Optional[str] = None


class BridgeGenerateRequest(BaseModel):
    target_project_root: Optional[str] = None
    override_launch_command: Optional[str] = None
    sync_logic_only: bool = False


class BridgeRescanRequest(BaseModel):
    target_project_root: Optional[str] = None
    override_launch_command: Optional[str] = None


class SidecarStatusResponse(BaseModel):
    thread_id: str
    target_project_root: str
    sidecar_root: str
    sidecar_exists: bool
    bridge_exists: bool
    ui_exists: bool
    protocol_exists: bool


def _normalize_plan_steps(raw_plan: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_plan, list):
        return []
    out: List[Dict[str, Any]] = []
    for step in raw_plan:
        if isinstance(step, dict):
            out.append(step)
    return out


def _build_thread_bridge_preview(thread_id: str, *, override_launch_command: Optional[str] = None) -> Dict[str, Any]:
    values, _checkpoint_id, _state_source = _load_thread_values(thread_id)

    repo_source = (values.get("repo_url") or "").strip()
    if not repo_source:
        raise HTTPException(status_code=400, detail="Current thread has empty repo_url.")
    user_notes = (values.get("user_notes") or "").strip()
    plan_steps = _normalize_plan_steps(values.get("plan"))

    preview = build_preview_from_source(
        repo_source=repo_source,
        user_notes=user_notes,
        plan_steps=plan_steps,
        override_launch_command=override_launch_command,
    )

    wp = ensure_thread_workplace(thread_id)
    generated_bridge_dir = Path(wp["thread_root"]) / "generated" / "bridge"
    generated_bridge_dir.mkdir(parents=True, exist_ok=True)
    preview_path = generated_bridge_dir / "bridge_preview.json"
    _write_json(preview_path, preview)

    return {
        "thread_id": thread_id,
        "preview": preview,
        "preview_path": str(preview_path),
    }


def _resolve_bridge_target_project_root(thread_id: str, target_project_root_raw: Optional[str]) -> Path:
    target_root_raw = (target_project_root_raw or "").strip()
    if target_root_raw:
        target_project_root = _resolve_target_dir(target_root_raw)
    else:
        values, _checkpoint_id, _state_source = _load_thread_values(thread_id)
        repo_source = (values.get("repo_url") or "").strip()
        target_project_root = resolve_local_project_root_from_repo_source(repo_source) if repo_source else None
        if target_project_root is None:
            raise HTTPException(
                status_code=400,
                detail="target_project_root is required when repo source cannot be resolved to a local directory.",
            )
    target_project_root.mkdir(parents=True, exist_ok=True)
    return target_project_root


def _sidecar_status(thread_id: str, target_project_root: Path) -> Dict[str, Any]:
    sidecar_root = (target_project_root / ".aui-dashboard").resolve()
    bridge_dir = sidecar_root / "bridge"
    ui_dir = sidecar_root / "ui"
    protocol_path = bridge_dir / "aui_ui_protocol.json"
    return {
        "thread_id": thread_id,
        "target_project_root": str(target_project_root),
        "sidecar_root": str(sidecar_root),
        "sidecar_exists": sidecar_root.exists(),
        "bridge_exists": bridge_dir.exists(),
        "ui_exists": ui_dir.exists(),
        "protocol_exists": protocol_path.exists(),
    }


@app.post("/rollback")
def rollback(req: RollbackRequest) -> Dict[str, Any]:
    if bool(req.checkpoint_id) == bool(req.step_id):
        raise HTTPException(status_code=400, detail="Exactly one of `checkpoint_id` or `step_id` must be provided.")

    base_config = {"configurable": {"thread_id": req.thread_id}}

    if req.checkpoint_id:
        target_config = {
            "configurable": {
                "thread_id": req.thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": req.checkpoint_id,
            }
        }
    else:
        snapshots = list(graph.get_state_history(base_config))
        chosen = None
        for s in snapshots[::-1]:
            values = getattr(s, "values", {}) or {}
            if values.get("last_step_id") == req.step_id:
                chosen = s
                break
        if chosen is None:
            raise HTTPException(status_code=404, detail=f"Cannot find checkpoint with last_step_id={req.step_id}")
        target_config = chosen.config

    base_snapshot = graph.get_state(target_config)
    base_values = base_snapshot.values or {}
    base_last_step_id = base_values.get("last_step_id")

    try:
        updated_config = graph.update_state(
            target_config,
            {
                "user_notes": req.user_notes,
                "context_ui": _normalize_context_ui(req.context_ui, req.user_notes),
                "image_url": req.image_url,
                "replan_required": True,
            },
        )
    except Exception as e:
        _raise_upstream_friendly_error(e, action="rollback")

    try:
        steps_completed = _stream_until_steps(
            stream_input=None,
            config=updated_config,
            max_steps=max(1, int(req.max_steps)),
            initial_last_step_id=base_last_step_id,
        )
    except Exception as e:
        _raise_upstream_friendly_error(e, action="rollback")

    final_snapshot = graph.get_state(updated_config)
    final_values = final_snapshot.values or {}
    ui_artifacts = _persist_ui_artifacts(
        req.thread_id,
        final_values,
        checkpoint_id=_checkpoint_id_from_config(final_snapshot.config or {}),
    )
    return {
        "thread_id": req.thread_id,
        "from_checkpoint_id": _checkpoint_id_from_config(target_config),
        "to_checkpoint_id": _checkpoint_id_from_config(updated_config),
        "current_step_index": final_values.get("current_step_index"),
        "last_step_id": final_values.get("last_step_id"),
        "steps_completed_this_call": steps_completed,
        "ui_artifacts": ui_artifacts,
    }


@app.post("/run")
def run(req: RunRequest) -> Dict[str, Any]:
    thread_id = req.thread_id or generate_thread_id(req.thread_name)
    try:
        wp = ensure_thread_workplace(thread_id)
    except Exception as e:
        _raise_upstream_friendly_error(e, action="run")
    config = {"configurable": {"thread_id": thread_id}}

    existing_snapshot = graph.get_state(config)
    existing_values = existing_snapshot.values or {}

    is_existing_thread = bool(req.thread_id and existing_values)
    initial_last_step_id = existing_values.get("last_step_id")

    if is_existing_thread:
        stream_input = None
        if req.user_notes.strip():
            graph.update_state(config, {"user_notes": req.user_notes.strip()})
    else:
        stream_input = {
            "repo_url": req.repo_url.strip(),
            "user_notes": (req.user_notes or "").strip(),
            "plan": [],
            "current_step_index": 0,
            "last_step_id": None,
            "step_outputs": {},
            "replan_required": False,
            "context_ui": None,
            "image_url": None,
        }
        initial_last_step_id = None

    try:
        steps_completed = _stream_until_steps(
            stream_input=stream_input,
            config=config,
            max_steps=max(1, int(req.max_steps)),
            initial_last_step_id=initial_last_step_id,
        )
    except Exception as e:
        _raise_upstream_friendly_error(e, action="run")

    snapshot = graph.get_state(config)
    values = snapshot.values or {}
    ui_artifacts = _persist_ui_artifacts(
        thread_id,
        values,
        checkpoint_id=_checkpoint_id_from_config(snapshot.config or {}),
    )
    return {
        "thread_id": thread_id,
        "plan_size": len(values.get("plan") or []),
        "current_step_index": values.get("current_step_index"),
        "last_step_id": values.get("last_step_id"),
        "steps_completed_this_call": steps_completed,
        "workplace": wp,
        "ui_artifacts": ui_artifacts,
    }


@app.get("/threads/{thread_id}/ui-artifacts")
def get_thread_ui_artifacts(thread_id: str) -> Dict[str, Any]:
    wp = ensure_thread_workplace(thread_id)
    ui_dir = Path(wp["ui_dir"])
    latest_ui_path = ui_dir / "latest_ui.json"
    timeline_path = ui_dir / "timeline.json"

    latest_ui = None
    timeline = None
    if latest_ui_path.exists():
        try:
            latest_ui = json.loads(latest_ui_path.read_text(encoding="utf-8"))
        except Exception:
            latest_ui = None
    if timeline_path.exists():
        try:
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        except Exception:
            timeline = None

    return {
        "thread_id": thread_id,
        "ui_dir": str(ui_dir),
        "files": _list_ui_files(ui_dir),
        "latest_ui": latest_ui,
        "timeline": timeline,
    }


@app.post("/threads/{thread_id}/export-ui")
def export_thread_ui(thread_id: str, req: ExportUIRequest) -> Dict[str, Any]:
    wp = ensure_thread_workplace(thread_id)
    source_ui_dir = Path(wp["ui_dir"]).resolve()
    if not source_ui_dir.exists():
        raise HTTPException(status_code=404, detail=f"UI directory not found for thread: {thread_id}")
    has_target_dir = bool((req.target_dir or "").strip())
    has_project_root = bool((req.target_project_root or "").strip())
    if has_target_dir and has_project_root:
        raise HTTPException(
            status_code=400,
            detail="Use either `target_dir` or `target_project_root`, not both.",
        )
    if not has_target_dir and not has_project_root:
        raise HTTPException(
            status_code=400,
            detail="One of `target_dir` or `target_project_root` is required.",
        )

    integration: Optional[Dict[str, str]] = None
    if has_project_root:
        project_root = _resolve_target_dir(req.target_project_root or "")
        project_root.mkdir(parents=True, exist_ok=True)
        target_dir = (project_root / ".aui-dashboard" / "ui" / thread_id).resolve()
    else:
        target_dir = _resolve_target_dir(req.target_dir or "")

    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        if req.mode == "sync":
            _sync_directory(source_ui_dir, target_dir)
        else:
            shutil.copytree(source_ui_dir, target_dir, dirs_exist_ok=True)
        if has_project_root:
            integration = _write_project_integration_files(
                thread_id=thread_id,
                project_root=project_root,
                integration_ui_dir=target_dir,
                mode=req.mode,
            )
    except Exception as e:
        _raise_upstream_friendly_error(e, action="export-ui")

    resp: Dict[str, Any] = {
        "thread_id": thread_id,
        "mode": req.mode,
        "source_ui_dir": str(source_ui_dir),
        "target_dir": str(target_dir),
        "exported_files": _list_ui_files(source_ui_dir),
    }
    if integration:
        resp["integration"] = integration
    return resp


@app.post("/bridge/preview")
def preview_bridge(req: BridgePreviewRequest) -> Dict[str, Any]:
    try:
        repo_source = normalize_repo_source(req.repo_url, req.repo_source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        plan_obj = generate_steps_from_repo(PlanRequest(repo_url=repo_source, user_notes=(req.user_notes or "").strip() or None))
        plan_steps = []
        for s in plan_obj.steps:
            if hasattr(s, "model_dump"):
                plan_steps.append(s.model_dump())
            else:
                plan_steps.append(s.dict())  # type: ignore[union-attr]
        preview = build_preview_from_source(
            repo_source=repo_source,
            user_notes=(req.user_notes or "").strip(),
            plan_steps=plan_steps,
            override_launch_command=req.override_launch_command,
        )
    except Exception as e:
        _raise_upstream_friendly_error(e, action="bridge-preview")

    resp: Dict[str, Any] = {"preview": preview}
    if req.thread_id:
        try:
            wp = ensure_thread_workplace(req.thread_id)
            generated_bridge_dir = Path(wp["thread_root"]) / "generated" / "bridge"
            generated_bridge_dir.mkdir(parents=True, exist_ok=True)
            preview_path = generated_bridge_dir / "bridge_preview.json"
            _write_json(preview_path, preview)
            resp["thread_id"] = req.thread_id
            resp["preview_path"] = str(preview_path)
        except Exception as e:
            _raise_upstream_friendly_error(e, action="bridge-preview")
    return resp


@app.get("/threads/{thread_id}/bridge-preview")
def get_thread_bridge_preview(thread_id: str) -> Dict[str, Any]:
    try:
        return _build_thread_bridge_preview(thread_id)
    except Exception as e:
        _raise_upstream_friendly_error(e, action="thread-bridge-preview")


@app.get("/threads/{thread_id}/sidecar-status", response_model=SidecarStatusResponse)
def get_thread_sidecar_status(thread_id: str, target_project_root: Optional[str] = None) -> SidecarStatusResponse:
    try:
        resolved_target_root = _resolve_bridge_target_project_root(thread_id, target_project_root)
        status = _sidecar_status(thread_id, resolved_target_root)
        return SidecarStatusResponse(**status)
    except Exception as e:
        _raise_upstream_friendly_error(e, action="sidecar-status")


@app.post("/threads/{thread_id}/generate-bridge")
def generate_thread_bridge(thread_id: str, req: BridgeGenerateRequest) -> Dict[str, Any]:
    try:
        target_project_root = _resolve_bridge_target_project_root(thread_id, req.target_project_root)
        target_bridge_dir = (target_project_root / ".aui-dashboard" / "bridge").resolve()

        if req.sync_logic_only:
            sidecar = _sidecar_status(thread_id, target_project_root)
            if not sidecar.get("sidecar_exists"):
                raise HTTPException(status_code=400, detail="Cannot sync logic only: .aui-dashboard does not exist.")
            if not sidecar.get("protocol_exists"):
                raise HTTPException(
                    status_code=400,
                    detail="Cannot sync logic only: existing aui_ui_protocol.json not found.",
                )
            target_write = sync_sidecar_logic_only(target_bridge_dir=target_bridge_dir)
            preview_path = None
        else:
            preview_result = _build_thread_bridge_preview(thread_id, override_launch_command=req.override_launch_command)
            preview = preview_result["preview"]
            target_write = write_bridge_bundle(
                target_bridge_dir=target_bridge_dir,
                thread_id=thread_id,
                project_root=target_project_root,
                preview=preview,
            )
            preview_path = preview_result.get("preview_path")

        wp = ensure_thread_workplace(thread_id)
        workplace_bridge_dir = Path(wp["thread_root"]) / "generated" / "bridge"
        if req.sync_logic_only:
            workplace_write = sync_sidecar_logic_only(target_bridge_dir=workplace_bridge_dir)
        else:
            workplace_write = write_bridge_bundle(
                target_bridge_dir=workplace_bridge_dir,
                thread_id=thread_id,
                project_root=target_project_root,
                preview=preview,
            )
    except Exception as e:
        _raise_upstream_friendly_error(e, action="generate-bridge")

    return {
        "thread_id": thread_id,
        "target_project_root": str(target_project_root),
        "target_bridge_dir": str(target_bridge_dir),
        "preview_path": preview_path,
        "operation_mode": "sync_logic_only" if req.sync_logic_only else "full_generate",
        "protocol_preserved": bool(req.sync_logic_only),
        "target_files": target_write,
        "workplace_files": workplace_write,
    }


@app.post("/threads/{thread_id}/bridge-rescan")
def rescan_thread_bridge(thread_id: str, req: BridgeRescanRequest) -> Dict[str, Any]:
    try:
        preview_result = _build_thread_bridge_preview(thread_id, override_launch_command=req.override_launch_command)
        preview = preview_result["preview"]

        values, _checkpoint_id, _state_source = _load_thread_values(thread_id)
        repo_source = (values.get("repo_url") or "").strip()

        target_root_raw = (req.target_project_root or "").strip()
        if target_root_raw:
            target_project_root = _resolve_target_dir(target_root_raw)
        else:
            target_project_root = resolve_local_project_root_from_repo_source(repo_source) if repo_source else None
            if target_project_root is None:
                raise HTTPException(
                    status_code=400,
                    detail="Rescan without target_project_root only supports local project sources.",
                )

        target_project_root.mkdir(parents=True, exist_ok=True)
        target_bridge_dir = (target_project_root / ".aui-dashboard" / "bridge").resolve()
        target_write = write_bridge_bundle(
            target_bridge_dir=target_bridge_dir,
            thread_id=thread_id,
            project_root=target_project_root,
            preview=preview,
        )
    except Exception as e:
        _raise_upstream_friendly_error(e, action="bridge-rescan")

    return {
        "thread_id": thread_id,
        "repo_source": repo_source,
        "target_project_root": str(target_project_root),
        "target_bridge_dir": str(target_bridge_dir),
        "preview_path": preview_result.get("preview_path"),
        "target_files": target_write,
        "preview": preview,
    }


@app.get("/history")
def history(thread_id: str) -> List[Dict[str, Any]]:
    base_config = {"configurable": {"thread_id": thread_id}}
    snapshots = list(graph.get_state_history(base_config))
    return _build_anchor_items(snapshots)


@app.get("/threads/{thread_id}/anchors")
def get_thread_anchors(thread_id: str) -> Dict[str, Any]:
    values, checkpoint_id, state_source = _load_thread_values(thread_id)

    if state_source == "graph":
        config = {"configurable": {"thread_id": thread_id}}
        snapshots = list(graph.get_state_history(config))
        anchors = [x for x in _build_anchor_items(snapshots) if x.get("checkpoint_id") and x.get("last_step_id")]
    else:
        anchors = _build_timeline_anchor_items(values)

    plan = values.get("plan") or []
    step_outputs = values.get("step_outputs") or {}
    total_steps = len(plan)
    completed_steps = len(step_outputs) if isinstance(step_outputs, dict) else 0

    current_step_index_raw = values.get("current_step_index")
    current_step_index = int(current_step_index_raw) if isinstance(current_step_index_raw, int) else 0

    current_step = None
    next_step = None
    if 0 <= current_step_index < total_steps and isinstance(plan[current_step_index], dict):
        current_step = plan[current_step_index]
    if 0 <= current_step_index + 1 < total_steps and isinstance(plan[current_step_index + 1], dict):
        next_step = plan[current_step_index + 1]

    progress_percent = round((completed_steps / total_steps) * 100, 2) if total_steps > 0 else 0.0

    return {
        "thread_id": thread_id,
        "checkpoint_id": checkpoint_id,
        "state_source": state_source,
        "total_steps": total_steps,
        "completed_steps": completed_steps,
        "progress_percent": progress_percent,
        "current_step_index": values.get("current_step_index"),
        "current_step": current_step,
        "next_step": next_step,
        "last_step_id": values.get("last_step_id"),
        "anchors": anchors,
    }


@app.get("/threads/{thread_id}/state")
def get_thread_state(thread_id: str) -> Dict[str, Any]:
    values, checkpoint_id, state_source = _load_thread_values(thread_id)
    return {
        "thread_id": thread_id,
        "checkpoint_id": checkpoint_id,
        "state_source": state_source,
        "values": values,
    }
