#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent
BRIDGE_DIR = (UI_DIR.parent / "bridge").resolve()
PROTOCOL_PATH = BRIDGE_DIR / "aui_ui_protocol.json"
CONFIG_PATH = BRIDGE_DIR / "aui_bridge_config.json"
LOG_PATH = BRIDGE_DIR / "bridge_runtime.log.jsonl"
ANCHOR_LOG_PATH = BRIDGE_DIR / "bridge_anchor_snapshots.jsonl"
INDEX_PATH = UI_DIR / "index.html"

# 修改 ASSETS_DIR 的定义
# 使用 r"" 原始字符串防止转义，或者使用正斜杠
BASE_DIR = Path(__file__).resolve().parent
# 指向你指定的 assets 目录
ASSETS_DIR = Path(r".\AI-Native Stateful UI Framework\bridge_template_assets\assets")

app = FastAPI(title="AIUI Real-time Runner", version="3")

# 确保目录存在并挂载
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
else:
    print(f"警告: 资源目录未找到: {ASSETS_DIR}")

if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

from aui_bridge import (  # type: ignore  # noqa: E402
    ConPTYSession,
    _now_iso,
    _resolve_command_base,
    _resolve_project_root,
    _to_flag_args,
)


app = FastAPI(title="AIUI Real-time Runner", version="3")
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

LOG_LOCK = threading.Lock()
ANCHOR_LOCK = threading.Lock()
_SENSITIVE_HINT_RE = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|authorization|\u5bc6\u7801|\u53e3\u4ee4|\u9a8c\u8bc1\u7801)"
)
_ANSI_ESCAPE_RE = re.compile(
    r"(?:\x1B[@-Z\\-_]|\x1B\[[0-?]*[ -/]*[@-~]|\x1B\][^\x07\x1B]*(?:\x07|\x1B\\))"
)
_ANCHOR_ID_SEQ_RE = re.compile(r"^anchor_(\d+)$", re.IGNORECASE)
_ENV_ALLOWLIST_PREFIXES = ("PYTHON", "VIRTUAL_ENV", "CONDA", "PATH", "PROMPT", "TERM")
_ENV_ALLOWLIST_KEYS = {
    "PATH",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "PYTHONIOENCODING",
    "COMSPEC",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
}
_ANCHOR_MAX_BYTES = 100 * 1024 * 1024
_ANCHOR_TRIM_TARGET_BYTES = 80 * 1024 * 1024
_ANCHOR_DEFAULT_PAGE_SIZE = 8
_ANCHOR_MAX_PAGE_SIZE = 64
_JSONL_WRITE_RETRY = 5
_JSONL_LOCK_WAIT_SEC = 0.08
_SNAPSHOT_EXCLUDE_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    ".aui-dashboard",
}
_IDEMPOTENT_CONFLICT_RE = re.compile(
    r"(?i)(file\s+exists|already\s+exists|overwrite\??\s*(?:\[[^\]]+\])?|replace\??\s*(?:\[[^\]]+\])?)"
)


def _looks_sensitive(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(_SENSITIVE_HINT_RE.search(text))


def _strip_ansi(value: Any) -> str:
    return _ANSI_ESCAPE_RE.sub("", str(value or ""))


def _contains_ansi(value: Any) -> bool:
    return bool(_ANSI_ESCAPE_RE.search(str(value or "")))


def _sanitize_event_for_log(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    evt = dict(payload or {})
    etype = str(evt.get("type") or "").lower()
    update = str(evt.get("update") or "").lower()
    stream = str(evt.get("stream") or "").lower()
    mode = str(evt.get("mode") or "").lower()
    input_type = str(evt.get("input_type") or "").lower()

    sensitive = bool(evt.get("_sensitive"))
    if input_type == "password" or mode == "password_input":
        sensitive = True

    probe_text = " ".join(
        [
            str(evt.get("line") or ""),
            str(evt.get("chunk") or ""),
            str(evt.get("payload") or ""),
            str(evt.get("prompt") or ""),
        ]
    )
    if _looks_sensitive(probe_text):
        sensitive = True

    ansi_stripped = False
    for key in ("line", "chunk", "payload", "prompt", "text", "data", "message"):
        if key not in evt:
            continue
        raw = str(evt.get(key) or "")
        cleaned = _strip_ansi(raw)
        if cleaned != raw:
            ansi_stripped = True
        evt[key] = cleaned
    if ansi_stripped:
        evt["ansi_stripped"] = True

    # Sensitive stdin should never be persisted.
    if etype == "stdio" and stream == "user_input" and sensitive:
        return None

    if update == "interaction_required" and sensitive:
        evt["line"] = "[REDACTED]"
        evt["prompt"] = "[REDACTED]"
        evt["redacted"] = True
        evt.pop("_sensitive", None)
        return evt

    if sensitive:
        for key in ("line", "chunk", "payload", "prompt", "text", "data"):
            if key in evt and str(evt.get(key) or ""):
                evt[key] = "[REDACTED]"
        evt["redacted"] = True

    evt.pop("_sensitive", None)
    return evt


def _append_runtime_log(payload: Dict[str, Any]) -> None:
    event = dict(payload or {})
    event.setdefault("ts", _now_iso())
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_LOCK:
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _anchor_seq_from_id(anchor_id: str) -> int:
    m = _ANCHOR_ID_SEQ_RE.match(str(anchor_id or "").strip())
    if not m:
        return 0
    with contextlib.suppress(Exception):
        return int(m.group(1))
    return 0


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _new_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _new_legacy_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _acquire_sidecar_file_lock(lock_path: Path, timeout_sec: float = 2.0) -> Optional[int]:
    deadline = time.monotonic() + max(0.2, timeout_sec)
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            with contextlib.suppress(Exception):
                os.write(fd, f"{os.getpid()} {time.time()}".encode("utf-8"))
            return fd
        except FileExistsError:
            time.sleep(_JSONL_LOCK_WAIT_SEC)
        except Exception:
            time.sleep(_JSONL_LOCK_WAIT_SEC)
    return None


def _release_sidecar_file_lock(fd: Optional[int], lock_path: Path) -> None:
    if fd is not None:
        with contextlib.suppress(Exception):
            os.close(fd)
    with contextlib.suppress(Exception):
        if lock_path.exists():
            lock_path.unlink()


def _load_jsonl_records(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with ANCHOR_LOCK:
            with path.open("r", encoding="utf-8") as f:
                for raw in f:
                    line = str(raw or "").strip()
                    if not line:
                        continue
                    with contextlib.suppress(Exception):
                        data = json.loads(line)
                        if isinstance(data, dict):
                            rows.append(data)
    except Exception:
        return rows
    return rows


def _append_jsonl_record(path: Path, payload: Dict[str, Any], lock: threading.Lock) -> None:
    rec = dict(payload or {})
    rec.setdefault("ts", _now_iso())
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")

    for attempt in range(_JSONL_WRITE_RETRY):
        fd: Optional[int] = None
        try:
            fd = _acquire_sidecar_file_lock(lock_path, timeout_sec=2.0 + attempt * 0.4)
            if fd is None:
                time.sleep(_JSONL_LOCK_WAIT_SEC * (attempt + 1))
                continue
            with lock:
                with path.open("a", encoding="utf-8", newline="\n") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
            return
        except Exception:
            time.sleep(_JSONL_LOCK_WAIT_SEC * (attempt + 1))
        finally:
            _release_sidecar_file_lock(fd, lock_path)


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _build_runtime_command(
    *,
    command_override: Optional[List[str]] = None,
    resume_flags: Optional[List[str]] = None,
) -> tuple[list[str], Path]:
    config = _load_json(CONFIG_PATH)
    default_command_base = list(config.get("command_base") or [])
    if not default_command_base:
        raise RuntimeError(f"Invalid config: command_base is empty ({CONFIG_PATH})")

    project_root = _resolve_project_root(BRIDGE_DIR, config)
    use_default_chain = not command_override
    command_base = list(command_override or default_command_base)
    command_base = _resolve_command_base(command_base, project_root, config)
    if not command_base:
        raise RuntimeError("Invalid runtime command: command_base is empty after resolution.")
    flag_defaults = dict(config.get("flag_defaults") or {})
    passthrough = list(config.get("passthrough_args") or [])
    extra_flags = list(resume_flags or [])
    # Hint child process to avoid rich terminal probing output.
    os.environ["TERM"] = "dumb"
    if use_default_chain:
        cmd = command_base + _to_flag_args(flag_defaults) + extra_flags + passthrough
    else:
        cmd = command_base + extra_flags
    return cmd, project_root


class IntentParser:
    _PROGRESS_RE = re.compile(r"(?i)(?:progress|\u8fdb\u5ea6)\s*[:\uff1a]?\s*(\d{1,3})\s*%")
    _INPUT_RE = re.compile(
        r"(?i)(enter\s+password|password\s*:|\u8bf7\u8f93\u5165|\u8f93\u5165\u5bc6\u7801|press\s+enter|wait\s+for\s+user|input\s*:|you\s*:)")
    _PASSWORD_RE = re.compile(r"(?i)(password|passwd|\u5bc6\u7801)")
    _ERROR_RE = re.compile(
        r"(?i)(traceback|exception|error|failed|fatal|module\s*not\s*found|no such file|permission denied)")

    def __init__(self, protocol: Dict[str, Any]):
        self.protocol = protocol if isinstance(protocol, dict) else {}
        self.is_layered = bool(self.protocol.get("is_layered", True))
        self.stages = self._load_stages(self.protocol)
        self.intent_rules = self._load_intent_rules(self.protocol)
        self.current_stage_index = -1
        self._carry = ""
        self._last_error_sig = ""

    def set_stage(self, index: int) -> None:
        self.current_stage_index = int(index)

    def parse_text(self, text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        payload = self._carry + text
        lines = re.split(r"\r?\n", payload)
        if payload.endswith("\n") or payload.endswith("\r"):
            self._carry = ""
        else:
            self._carry = lines.pop() if lines else payload

        out: List[Dict[str, Any]] = []
        for raw in lines:
            line = str(raw or "").strip()
            if not line:
                continue
            out.extend(self.parse_line(line))
        return out

    def parse_line(self, line: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []

        # Protocol-driven intent rules (SDUI-first).
        for rule in self.intent_rules:
            pattern = rule.get("pattern")
            emit = rule.get("emit")
            if not pattern or not isinstance(emit, dict):
                continue

            groups: List[str] = []
            try:
                m = pattern.search(line)
            except Exception:
                m = None
            if m is None:
                continue
            groups = list(m.groups()) if m.groups() else []

            payload: Dict[str, Any] = dict(emit)
            payload.setdefault("type", "ui_state")
            payload.setdefault("line", line)

            if "progress_group" in payload:
                try:
                    gidx = int(payload.pop("progress_group"))
                    if gidx <= 0 or len(groups) < gidx:
                        raise IndexError(f"progress_group {gidx} out of range")
                    grow = str(groups[gidx - 1]).strip()
                    if not re.fullmatch(r"\d{1,3}", grow):
                        raise ValueError(f"progress_group value is not numeric: {grow!r}")
                    payload["progress"] = max(0, min(100, int(grow)))
                except Exception:
                    # Drop broken progress payload instead of emitting invalid UI state.
                    payload.pop("progress", None)

            if "stage_index" in payload:
                if not self.is_layered:
                    # When layered mode is disabled, skip stage-change signals.
                    continue
                try:
                    payload["current_stage_index"] = int(payload["stage_index"])
                except Exception:
                    pass
                payload.setdefault("current_stage_id", payload.get("stage_id"))
                payload.setdefault("current_stage_label", payload.get("stage_label"))
                if "current_stage_index" in payload:
                    self.current_stage_index = int(payload["current_stage_index"])

            if str(payload.get("update") or "") == "error_detected":
                sig = line.strip().lower()
                if sig == self._last_error_sig:
                    continue
                self._last_error_sig = sig

            out.append(payload)

        # Backward-compatible built-in fallbacks.
        pm = self._PROGRESS_RE.search(line)
        if pm and not any(str(x.get("update") or "") == "progress" for x in out):
            with contextlib.suppress(Exception):
                progress = max(0, min(100, int(pm.group(1))))
                out.append(
                    {
                        "type": "ui_state",
                        "update": "progress",
                        "progress": progress,
                        "line": line,
                    }
                )

        stage_index = self._match_stage(line)
        if self.is_layered and stage_index is not None and stage_index != self.current_stage_index:
            self.current_stage_index = int(stage_index)
            stage = self.stages[stage_index]
            if not any(str(x.get("update") or "") == "stage_change" for x in out):
                out.append(
                    {
                        "type": "ui_state",
                        "update": "stage_change",
                        "current_stage_index": stage_index,
                        "current_stage_id": stage["id"],
                        "current_stage_label": stage["label"],
                        "line": line,
                    }
                )

        if self._INPUT_RE.search(line):
            if not any(str(x.get("update") or "") == "interaction_required" for x in out):
                is_password = bool(self._PASSWORD_RE.search(line))
                out.append(
                    {
                        "type": "ui_state",
                        "update": "interaction_required",
                        "mode": "password_input" if is_password else "input",
                        "input_type": "password" if is_password else "text",
                        "line": line,
                    }
                )

        if self._ERROR_RE.search(line):
            sig = line.strip().lower()
            if sig != self._last_error_sig:
                self._last_error_sig = sig
                if not any(str(x.get("update") or "") == "error_detected" for x in out):
                    out.append(
                        {
                            "type": "ui_state",
                            "update": "error_detected",
                            "line": line,
                            "ask_auto_recover": True,
                        }
                    )

        return out

    def _match_stage(self, line: str) -> Optional[int]:
        if not self.is_layered:
            return None
        if not self.stages:
            return None
        start = max(0, self.current_stage_index + 1)
        ordered = list(range(start, len(self.stages))) + list(range(0, start))
        for idx in ordered:
            stage = self.stages[idx]
            patterns: List[re.Pattern[str]] = stage["patterns"]
            for pattern in patterns:
                with contextlib.suppress(Exception):
                    if pattern.search(line):
                        return idx
        return None

    def _load_stages(self, protocol: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.is_layered:
            return []
        status_bar = protocol.get("status_bar") if isinstance(protocol.get("status_bar"), dict) else {}
        raw_stages = status_bar.get("stages") if isinstance(status_bar.get("stages"), list) else []
        stages: List[Dict[str, Any]] = []
        for idx, raw in enumerate(raw_stages):
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("id") or f"stage_{idx + 1}")
            label = str(raw.get("label") or sid)
            compiled: List[re.Pattern[str]] = []

            trigger_keyword = str(raw.get("trigger_keyword") or "").strip()
            if trigger_keyword:
                with contextlib.suppress(Exception):
                    compiled.append(re.compile(re.escape(trigger_keyword), re.IGNORECASE))

            match_patterns = raw.get("match_patterns") if isinstance(raw.get("match_patterns"), list) else []
            for p in match_patterns:
                txt = str(p or "").strip()
                if not txt:
                    continue
                with contextlib.suppress(Exception):
                    compiled.append(re.compile(txt, re.IGNORECASE))

            if not compiled:
                with contextlib.suppress(Exception):
                    compiled.append(re.compile(re.escape(label), re.IGNORECASE))

            stages.append(
                {
                    "index": idx,
                    "id": sid,
                    "label": label,
                    "patterns": compiled,
                    "resume_command": raw.get("resume_command"),
                    "resume_flag": raw.get("resume_flag"),
                }
            )
        return stages

    def _load_intent_rules(self, protocol: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = protocol.get("intent_rules") if isinstance(protocol.get("intent_rules"), list) else []
        out: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            regex = str(item.get("regex") or "").strip()
            emit = item.get("emit")
            if not regex or not isinstance(emit, dict):
                continue
            with contextlib.suppress(Exception):
                out.append(
                    {
                        "id": str(item.get("id") or ""),
                        "regex": regex,
                        "pattern": re.compile(regex, re.IGNORECASE),
                        "emit": dict(emit),
                    }
                )
        return out

    def stage_payload(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for s in self.stages:
            row: Dict[str, Any] = {"index": s["index"], "id": s["id"], "label": s["label"]}
            if s.get("resume_command") is not None:
                row["resume_command"] = s.get("resume_command")
            if s.get("resume_flag") is not None:
                row["resume_flag"] = s.get("resume_flag")
            out.append(row)
        return out


class LiveBridge:
    def __init__(self) -> None:
        self.events: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=4096)
        self.config = _load_json(CONFIG_PATH)
        self.project_root: Path = _resolve_project_root(BRIDGE_DIR, self.config)
        self.snapshot_enabled: bool = bool(self.config.get("snapshot_enabled", False))
        self.work_dir: Path = self._resolve_work_dir(self.config)
        self.snapshot_root: Path = (BRIDGE_DIR.parent / "snapshots").resolve()
        self.protocol = _load_json(PROTOCOL_PATH)
        self.intent = IntentParser(self.protocol)
        self.session: Optional[ConPTYSession] = None
        self._wait_task: Optional[asyncio.Task[int]] = None
        self._lock = asyncio.Lock()
        self.input_records: List[Dict[str, Any]] = []
        self.last_success_stage_index: int = -1
        self.anchor_snapshots: List[Dict[str, Any]] = []
        self._anchor_seq: int = 0
        self._initial_input_anchor_created: bool = False
        self._is_replaying: bool = False
        self._last_stdout_emit_ts: float = time.monotonic()
        self._replay_auto_confirm_enabled: bool = False
        self._last_auto_confirm_ts: float = 0.0
        self._last_auto_confirm_sig: str = ""
        # Buffers raw stdin chunks per source and only commits records on newline.
        self._stdin_commit_buffers: Dict[str, str] = {}
        self.run_id: str = _new_run_id()
        self.startup_archive_info: Dict[str, Any] = {}
        anchor_settings = self.protocol.get("anchor_settings")
        page_size_raw = anchor_settings.get("page_size") if isinstance(anchor_settings, dict) else _ANCHOR_DEFAULT_PAGE_SIZE
        self.anchor_page_size: int = int(
            max(
                1,
                min(
                    _ANCHOR_MAX_PAGE_SIZE,
                    _safe_int(page_size_raw, _ANCHOR_DEFAULT_PAGE_SIZE),
                ),
            )
        )
        self._prepare_anchor_log_for_startup()
        self._load_anchor_snapshots_from_disk()
        self._trim_anchor_log_if_oversized()
        self._recompute_anchor_seq()

    def _resolve_work_dir(self, config: Dict[str, Any]) -> Path:
        raw = str(config.get("work_dir") or "").strip()
        if not raw:
            return self.project_root.resolve()
        path = Path(raw)
        if not path.is_absolute():
            path = (self.project_root / path).resolve()
        else:
            path = path.resolve()
        return path

    def _coerce_argv(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            out: List[str] = []
            for item in value:
                text = str(item or "").strip()
                if text:
                    out.append(text)
            return out
        text = str(value or "").strip()
        if not text:
            return []
        try:
            parsed = shlex.split(text, posix=False)
            out = [p for p in parsed if str(p or "").strip()]
            return out if out else [text]
        except Exception:
            return [text]

    def _target_stage_resume_plan(self, stage_index: int) -> Dict[str, Any]:
        if stage_index < 0 or stage_index >= len(self.intent.stages):
            return {"use_resume": False, "mode": "replay", "command_override": None, "resume_flags": []}
        stage = self.intent.stages[stage_index]
        resume_command = self._coerce_argv(stage.get("resume_command"))
        resume_flags = self._coerce_argv(stage.get("resume_flag"))
        if resume_command:
            return {
                "use_resume": True,
                "mode": "resume_command",
                "command_override": resume_command,
                "resume_flags": resume_flags,
            }
        if resume_flags:
            return {
                "use_resume": True,
                "mode": "resume_flag",
                "command_override": None,
                "resume_flags": resume_flags,
            }
        return {"use_resume": False, "mode": "replay", "command_override": None, "resume_flags": []}

    async def _maybe_auto_confirm_replay(self, text: str) -> None:
        if not self._replay_auto_confirm_enabled:
            return
        if self.session is None:
            return
        line = str(text or "")
        if not line or not _IDEMPOTENT_CONFLICT_RE.search(line):
            return
        now = time.monotonic()
        sig = re.sub(r"\s+", " ", line.strip().lower())[:180]
        if sig == self._last_auto_confirm_sig and (now - self._last_auto_confirm_ts) < 1.2:
            return
        self._last_auto_confirm_sig = sig
        self._last_auto_confirm_ts = now
        await self.session.send_stdin("y\n")
        await self._push_event(
            {
                "type": "warn",
                "message": "Auto-confirmed idempotent replay prompt with 'y'.",
            }
        )

    def _consume_committed_inputs(self, source: str, incoming: str) -> List[str]:
        key = str(source or "user")
        merged = str(self._stdin_commit_buffers.get(key) or "") + str(incoming or "")
        if not merged:
            self._stdin_commit_buffers[key] = ""
            return []

        committed: List[str] = []
        start = 0
        i = 0
        length = len(merged)
        while i < length:
            ch = merged[i]
            if ch == "\r":
                end = i + 1
                if end < length and merged[end] == "\n":
                    end += 1
                committed.append(merged[start:end])
                start = end
                i = end
                continue
            if ch == "\n":
                end = i + 1
                committed.append(merged[start:end])
                start = end
            i += 1

        self._stdin_commit_buffers[key] = merged[start:]
        return [c for c in committed if str(c).strip()]

    def _path_within(self, parent: Path, child: Path) -> bool:
        try:
            parent_resolved = parent.resolve()
            child_resolved = child.resolve()
        except Exception:
            return False
        try:
            return Path(os.path.commonpath([str(parent_resolved), str(child_resolved)])).resolve() == parent_resolved
        except Exception:
            return False

    def _snapshot_ignore(self, src: str, names: List[str]) -> List[str]:
        excluded = {n.lower() for n in _SNAPSHOT_EXCLUDE_NAMES}
        out: List[str] = [name for name in names if name.lower() in excluded]

        # Prevent self-recursive copy when snapshot folder is under work_dir.
        try:
            src_path = Path(src).resolve()
            if (
                self.snapshot_root.name
                and self.snapshot_root.name in names
                and src_path == self.snapshot_root.parent.resolve()
                and self.snapshot_root.name not in out
            ):
                out.append(self.snapshot_root.name)
        except Exception:
            pass
        return out

    def _create_physical_snapshot(self, anchor_id: str) -> bool:
        if not self.snapshot_enabled:
            return False
        aid = str(anchor_id or "").strip()
        if not aid:
            return False
        if not self.work_dir.exists() or not self.work_dir.is_dir():
            raise RuntimeError(f"work_dir does not exist or is not a directory: {self.work_dir}")

        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        snapshot_dir = (self.snapshot_root / aid).resolve()
        if not self._path_within(self.snapshot_root, snapshot_dir):
            raise RuntimeError(f"Refusing to create snapshot outside snapshot_root: {snapshot_dir}")

        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        shutil.copytree(self.work_dir, snapshot_dir, ignore=self._snapshot_ignore)
        return True

    def _clear_work_dir_for_restore(self) -> None:
        if not self.work_dir.exists() or not self.work_dir.is_dir():
            raise RuntimeError(f"work_dir does not exist or is not a directory: {self.work_dir}")
        if not self._path_within(self.project_root, self.work_dir):
            raise RuntimeError(f"Refusing to clear work_dir outside project_root: {self.work_dir}")

        protected = {n.lower() for n in _SNAPSHOT_EXCLUDE_NAMES}
        # Keep snapshot container itself when under work_dir.
        protected.add(self.snapshot_root.name.lower())

        for item in self.work_dir.iterdir():
            if item.name.lower() in protected:
                continue
            if item.is_symlink() or item.is_file():
                item.unlink()
                continue
            if item.is_dir():
                shutil.rmtree(item)
                continue
            item.unlink()

    def _restore_physical_snapshot(self, anchor_id: str) -> bool:
        if not self.snapshot_enabled:
            return False
        aid = str(anchor_id or "").strip()
        if not aid:
            return False

        snapshot_dir = (self.snapshot_root / aid).resolve()
        if not snapshot_dir.exists() or not snapshot_dir.is_dir():
            return False
        if not self._path_within(self.snapshot_root, snapshot_dir):
            raise RuntimeError(f"Refusing to restore from snapshot outside snapshot_root: {snapshot_dir}")

        self._clear_work_dir_for_restore()
        shutil.copytree(snapshot_dir, self.work_dir, dirs_exist_ok=True)
        return True

    def _prepare_anchor_log_for_startup(self) -> None:
        self.startup_archive_info = {}
        if not ANCHOR_LOG_PATH.exists():
            return

        reason = ""
        try:
            size = int(ANCHOR_LOG_PATH.stat().st_size)
            if size > _ANCHOR_MAX_BYTES:
                reason = "oversized"
            else:
                mdate = datetime.fromtimestamp(ANCHOR_LOG_PATH.stat().st_mtime).date()
                if mdate < datetime.now().date():
                    reason = "previous_day"
        except Exception:
            reason = ""

        if not reason:
            return
        archived = self._archive_anchor_log(reason=reason)
        if archived:
            self.startup_archive_info = {
                "archived": True,
                "reason": reason,
                "path": str(archived),
            }

    def _archive_anchor_log(self, *, reason: str) -> Optional[Path]:
        if not ANCHOR_LOG_PATH.exists():
            return None
        lock_path = ANCHOR_LOG_PATH.with_name(ANCHOR_LOG_PATH.name + ".lock")
        fd: Optional[int] = None
        try:
            fd = _acquire_sidecar_file_lock(lock_path, timeout_sec=4.0)
            if fd is None:
                return None
            if not ANCHOR_LOG_PATH.exists():
                return None

            stamp = _new_legacy_stamp()
            base = BRIDGE_DIR / f"anchors_legacy_{stamp}.jsonl.bak"
            target = base
            idx = 1
            while target.exists():
                target = BRIDGE_DIR / f"anchors_legacy_{stamp}_{idx:02d}.jsonl.bak"
                idx += 1
            ANCHOR_LOG_PATH.rename(target)
            _append_runtime_log(
                {
                    "type": "anchor_archive",
                    "reason": reason,
                    "target": str(target),
                    "ts": _now_iso(),
                }
            )
            return target
        except Exception:
            return None
        finally:
            _release_sidecar_file_lock(fd, lock_path)

    def _recompute_anchor_seq(self) -> None:
        max_seq = 0
        for anchor in self.anchor_snapshots:
            seq = _anchor_seq_from_id(str(anchor.get("anchor_id") or ""))
            if seq > max_seq:
                max_seq = seq
        if max_seq <= 0:
            max_seq = len(self.anchor_snapshots)
        self._anchor_seq = max_seq

    def _normalize_anchor_record(self, raw: Dict[str, Any], fallback_index: int) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        anchor_id = str(raw.get("anchor_id") or "").strip()
        if not anchor_id:
            anchor_id = f"anchor_{fallback_index:04d}"
        run_id = str(raw.get("run_id") or "legacy_unknown").strip() or "legacy_unknown"

        last_inputs = raw.get("last_inputs")
        if not isinstance(last_inputs, list):
            last_inputs = []

        env_vars = raw.get("env_vars")
        if not isinstance(env_vars, dict):
            env_vars = {}

        stage_index = _safe_int(raw.get("stage_index"), -1)
        out: Dict[str, Any] = {
            "anchor_id": anchor_id,
            "run_id": run_id,
            "ts": str(raw.get("ts") or _now_iso()),
            "reason": str(raw.get("reason") or "stage_change"),
            "stage_index": stage_index,
            "stage_id": str(raw.get("stage_id") or ""),
            "stage_label": str(raw.get("stage_label") or f"stage_{max(0, stage_index) + 1}"),
            "last_inputs": last_inputs,
            "input_count": _safe_int(raw.get("input_count"), len(last_inputs)),
            "env_vars": env_vars,
        }
        return out

    def _load_anchor_snapshots_from_disk(self) -> None:
        records = _load_jsonl_records(ANCHOR_LOG_PATH)
        loaded: List[Dict[str, Any]] = []
        for i, row in enumerate(records, start=1):
            normalized = self._normalize_anchor_record(row, fallback_index=i)
            if normalized is not None:
                loaded.append(normalized)
        self.anchor_snapshots = loaded

    def _anchor_for_disk(self, anchor: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(anchor or {})
        persisted_inputs: List[Dict[str, Any]] = []
        raw_inputs = data.get("last_inputs")
        if isinstance(raw_inputs, list):
            for item in raw_inputs:
                if not isinstance(item, dict):
                    continue
                persisted_inputs.append(dict(item))
        data["last_inputs"] = persisted_inputs
        data["input_count"] = len(persisted_inputs)
        data["run_id"] = str(data.get("run_id") or self.run_id)
        return data

    def _append_anchor_to_disk(self, anchor: Dict[str, Any]) -> None:
        _append_jsonl_record(ANCHOR_LOG_PATH, self._anchor_for_disk(anchor), ANCHOR_LOCK)

    def _trim_anchor_log_if_oversized(self) -> None:
        if not ANCHOR_LOG_PATH.exists():
            return
        try:
            size = int(ANCHOR_LOG_PATH.stat().st_size)
        except Exception:
            return
        if size <= _ANCHOR_MAX_BYTES:
            return
        lock_path = ANCHOR_LOG_PATH.with_name(ANCHOR_LOG_PATH.name + ".lock")
        fd: Optional[int] = None
        try:
            fd = _acquire_sidecar_file_lock(lock_path, timeout_sec=4.0)
            if fd is None:
                return
            with ANCHOR_LOCK:
                try:
                    lines = ANCHOR_LOG_PATH.read_text(encoding="utf-8").splitlines()
                except Exception:
                    return

                keep_rev: List[str] = []
                used = 0
                for line in reversed(lines):
                    row = str(line or "").strip()
                    if not row:
                        continue
                    row_bytes = len((row + "\n").encode("utf-8"))
                    if keep_rev and (used + row_bytes > _ANCHOR_TRIM_TARGET_BYTES):
                        break
                    used += row_bytes
                    keep_rev.append(row)

                keep_lines = list(reversed(keep_rev))
                try:
                    ANCHOR_LOG_PATH.write_text(
                        "".join(f"{row}\n" for row in keep_lines),
                        encoding="utf-8",
                    )
                except Exception:
                    return
        finally:
            _release_sidecar_file_lock(fd, lock_path)

        reloaded: List[Dict[str, Any]] = []
        for i, line in enumerate(keep_lines, start=1):
            with contextlib.suppress(Exception):
                obj = json.loads(line)
                if isinstance(obj, dict):
                    normalized = self._normalize_anchor_record(obj, fallback_index=i)
                    if normalized is not None:
                        reloaded.append(normalized)
        self.anchor_snapshots = reloaded

    def _persist_all_anchors_to_active_log(self) -> None:
        lock_path = ANCHOR_LOG_PATH.with_name(ANCHOR_LOG_PATH.name + ".lock")
        fd: Optional[int] = None
        try:
            fd = _acquire_sidecar_file_lock(lock_path, timeout_sec=4.0)
            if fd is None:
                return
            rows = [json.dumps(self._anchor_for_disk(a), ensure_ascii=False) for a in self.anchor_snapshots]
            with ANCHOR_LOCK:
                ANCHOR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                ANCHOR_LOG_PATH.write_text("".join(f"{row}\n" for row in rows), encoding="utf-8")
        except Exception:
            pass
        finally:
            _release_sidecar_file_lock(fd, lock_path)

    def _clear_legacy_backup_files(self) -> int:
        removed = 0
        for path in BRIDGE_DIR.glob("anchors_legacy_*.jsonl.bak"):
            with contextlib.suppress(Exception):
                path.unlink()
                removed += 1
        return removed

    def _paginate_anchors(self, *, limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
        page_size = self.anchor_page_size if limit is None else _safe_int(limit, self.anchor_page_size)
        page_size = max(1, min(_ANCHOR_MAX_PAGE_SIZE, page_size))
        start = max(0, _safe_int(offset, 0))

        ordered = [self._anchor_public_payload(a) for a in reversed(self.anchor_snapshots)]
        total = len(ordered)
        if start >= total:
            return {
                "anchors": [],
                "total_anchor_count": total,
                "has_more": False,
                "next_offset": total,
                "limit": page_size,
                "offset": start,
                "current_run_id": self.run_id,
            }

        end = min(total, start + page_size)
        page = ordered[start:end]
        return {
            "anchors": page,
            "total_anchor_count": total,
            "has_more": end < total,
            "next_offset": end,
            "limit": page_size,
            "offset": start,
            "current_run_id": self.run_id,
        }

    def _capture_env_snapshot(self) -> Dict[str, str]:
        env = os.environ.copy()
        out: Dict[str, str] = {}
        for key, value in env.items():
            ukey = str(key or "").upper()
            if ukey in _ENV_ALLOWLIST_KEYS or ukey.startswith(_ENV_ALLOWLIST_PREFIXES):
                sval = str(value or "")
                if _looks_sensitive(f"{ukey}={sval}") or _looks_sensitive(ukey):
                    out[ukey] = "[REDACTED]"
                else:
                    out[ukey] = sval
        return out

    def _snapshot_inputs(self, before_stage_index: Optional[int] = None) -> List[Dict[str, Any]]:
        target = before_stage_index
        out: List[Dict[str, Any]] = []
        for rec in self.input_records:
            stage_idx = int(rec.get("stage_index", -1))
            if target is not None and stage_idx >= int(target):
                continue
            out.append(
                {
                    "text": str(rec.get("text") or ""),
                    "stage_index": stage_idx,
                    "source": str(rec.get("source") or "user"),
                    "input_type": str(rec.get("input_type") or "text"),
                    "ts": str(rec.get("ts") or _now_iso()),
                }
            )
        return out

    def _next_anchor_id(self) -> str:
        self._anchor_seq += 1
        return f"anchor_{self._anchor_seq:04d}"

    def _build_anchor(
        self,
        *,
        stage_index: int,
        stage_id: str = "",
        stage_label: str = "",
        reason: str = "stage_change",
    ) -> Dict[str, Any]:
        inputs = self._snapshot_inputs(before_stage_index=stage_index + 1)
        anchor = {
            "anchor_id": self._next_anchor_id(),
            "run_id": self.run_id,
            "ts": _now_iso(),
            "reason": str(reason or "stage_change"),
            "stage_index": int(stage_index),
            "stage_id": str(stage_id or ""),
            "stage_label": str(stage_label or f"stage_{int(stage_index) + 1}"),
            "last_inputs": inputs,
            "input_count": len(inputs),
            "env_vars": self._capture_env_snapshot(),
        }
        return anchor

    def _anchor_public_payload(self, anchor: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(anchor.get("run_id") or "legacy_unknown")
        scope = "current" if run_id == self.run_id else "history"
        if run_id.startswith("legacy_"):
            scope = "legacy"
        return {
            "anchor_id": str(anchor.get("anchor_id") or ""),
            "run_id": run_id,
            "ts": str(anchor.get("ts") or _now_iso()),
            "reason": str(anchor.get("reason") or ""),
            "stage_index": int(anchor.get("stage_index", -1)),
            "stage_id": str(anchor.get("stage_id") or ""),
            "stage_label": str(anchor.get("stage_label") or ""),
            "input_count": int(anchor.get("input_count", 0)),
            "env_var_count": len(anchor.get("env_vars") or {}),
            "session_scope": scope,
        }

    async def _create_anchor_event(
        self,
        *,
        stage_index: int,
        stage_id: str = "",
        stage_label: str = "",
        reason: str = "stage_change",
    ) -> Dict[str, Any]:
        if stage_index < 0:
            stage_index = 0
        anchor = self._build_anchor(
            stage_index=stage_index,
            stage_id=stage_id,
            stage_label=stage_label,
            reason=reason,
        )
        self.anchor_snapshots.append(anchor)
        self._append_anchor_to_disk(anchor)
        self._trim_anchor_log_if_oversized()
        self._recompute_anchor_seq()
        if self.snapshot_enabled:
            aid = str(anchor.get("anchor_id") or "").strip()
            if aid:
                try:
                    await asyncio.to_thread(self._create_physical_snapshot, aid)
                except Exception as exc:
                    await self._push_event(
                        {
                            "type": "warn",
                            "message": f"Workspace snapshot creation failed for {aid}: {exc}",
                        }
                    )
        page = self._paginate_anchors(limit=self.anchor_page_size, offset=0)
        payload = {
            "type": "ui_state",
            "update": "anchor_created",
            "anchor": self._anchor_public_payload(anchor),
            "anchor_count": int(page.get("total_anchor_count", len(self.anchor_snapshots))),
            "has_more": bool(page.get("has_more", False)),
            "next_offset": int(page.get("next_offset", 0)),
            "current_run_id": self.run_id,
        }
        await self._push_event(payload)
        return anchor

    def _anchors_catalog(
        self,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Dict[str, Any]:
        return self._paginate_anchors(limit=limit, offset=offset)

    def _find_anchor_for_target(self, target_stage_index: int) -> Optional[Dict[str, Any]]:
        target = int(target_stage_index)
        same_run = [
            a
            for a in self.anchor_snapshots
            if int(a.get("stage_index", -1)) < target and str(a.get("run_id") or "") == self.run_id
        ]
        if same_run:
            same_run.sort(key=lambda x: int(x.get("stage_index", -1)), reverse=True)
            return same_run[0]
        candidates = [a for a in self.anchor_snapshots if int(a.get("stage_index", -1)) < target]
        if not candidates:
            return None
        candidates.sort(key=lambda x: int(x.get("stage_index", -1)), reverse=True)
        return candidates[0]

    def _find_anchor_by_id(self, anchor_id: str) -> Optional[Dict[str, Any]]:
        aid = str(anchor_id or "").strip()
        if not aid:
            return None
        for anchor in reversed(self.anchor_snapshots):
            if str(anchor.get("anchor_id") or "") == aid:
                return anchor
        return None

    def _progress_for_stage(self, stage_index: int) -> float:
        total = len(self.intent.stages)
        if total <= 0:
            return 0.0
        idx = int(stage_index)
        if idx < 0:
            return 0.0
        idx = max(0, min(total - 1, idx))
        return max(0.0, min(100.0, ((idx + 1) / total) * 100.0))

    async def wait_for_quiet(self, *, quiet_ms: int = 800, max_wait_sec: float = 8.0) -> None:
        quiet_sec = max(0.05, float(quiet_ms) / 1000.0)
        deadline = time.monotonic() + max(0.2, float(max_wait_sec))
        while True:
            now = time.monotonic()
            quiet_for = now - float(self._last_stdout_emit_ts)
            if quiet_for >= quiet_sec:
                return
            if now >= deadline:
                return
            await asyncio.sleep(0.05)

    def _decorate_ui_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        evt = dict(payload or {})
        etype = str(evt.get("type") or "").lower()
        update = str(evt.get("update") or "").lower()
        stream = str(evt.get("stream") or "").lower()

        if etype == "ui_state" and update == "interaction_required":
            mode = str(evt.get("mode") or "").lower()
            if mode in {"password", "password_input"}:
                evt["mode"] = "password_input"
                evt["input_type"] = "password"
                evt["_sensitive"] = True
            else:
                evt.setdefault("mode", "input")
                evt.setdefault("input_type", "text")

        if etype == "stdio" and stream == "user_input":
            input_type = str(evt.get("input_type") or "").lower()
            if input_type == "password" or _looks_sensitive(evt.get("line") or evt.get("chunk")):
                evt["_sensitive"] = True

        return evt

    async def _push_event(self, payload: Dict[str, Any]) -> None:
        evt = self._decorate_ui_state(payload)
        allow_during_replay_visible = bool(evt.pop("_allow_during_replay_visible", False))
        evt.setdefault("ts", _now_iso())
        log_evt = _sanitize_event_for_log(evt)
        if log_evt is not None:
            _append_runtime_log(log_evt)

        out_evt = dict(evt)
        if bool(evt.get("_sensitive")):
            if str(out_evt.get("type") or "").lower() == "stdio" and str(out_evt.get("stream") or "").lower() == "user_input":
                if str(out_evt.get("line") or ""):
                    out_evt["line"] = "[REDACTED]"
                if str(out_evt.get("chunk") or ""):
                    out_evt["chunk"] = "[REDACTED]"
        if self._is_replaying and not allow_during_replay_visible:
            out_evt["replaying"] = True
        out_evt.pop("_sensitive", None)
        if self.events.full():
            try:
                self.events.get_nowait()
            except Exception:
                pass
        await self.events.put(out_evt)

    async def _emit(self, event: Dict[str, Any]) -> None:
        etype = str(event.get("type") or "").lower()
        forward_event = dict(event or {})
        if etype == "raw":
            forward_event = {
                "type": "stdio",
                "stream": "stdout",
                "chunk": str(event.get("data") or ""),
                "ts": str(event.get("ts") or _now_iso()),
            }
        await self._push_event(forward_event)
        if etype not in {"stdio", "raw"}:
            return

        if etype == "raw":
            self._last_stdout_emit_ts = time.monotonic()
        elif etype == "stdio":
            stream = str(forward_event.get("stream") or "").lower()
            if stream == "stdout":
                self._last_stdout_emit_ts = time.monotonic()

        text = str(forward_event.get("chunk") or forward_event.get("line") or event.get("data") or "")
        # During rollback replay, keep raw stream only and skip intent parsing
        # to prevent old-stage markers from contaminating current UI progress.
        if self._is_replaying:
            if text:
                await self._maybe_auto_confirm_replay(text)
            return

        if not text:
            return
        prev_stage_index = int(self.intent.current_stage_index)
        for parsed in self.intent.parse_text(text):
            if str(parsed.get("update") or "") == "stage_change":
                # Update by previous stage when switching stage:
                # once a new stage is reached, previous stage is considered successful.
                if prev_stage_index >= 0:
                    self.last_success_stage_index = prev_stage_index
                    stage_meta: Dict[str, Any] = {}
                    if 0 <= prev_stage_index < len(self.intent.stages):
                        stage_meta = self.intent.stages[prev_stage_index]
                    await self._create_anchor_event(
                        stage_index=prev_stage_index,
                        stage_id=str(stage_meta.get("id") or ""),
                        stage_label=str(stage_meta.get("label") or f"stage_{prev_stage_index + 1}"),
                        reason="stage_completed",
                    )
                try:
                    prev_stage_index = int(parsed.get("current_stage_index", prev_stage_index))
                except Exception:
                    pass
            if str(parsed.get("update") or "") == "error_detected":
                fallback = self.last_success_stage_index
                if fallback < 0:
                    fallback = max(0, int(self.intent.current_stage_index) - 1)
                parsed["rollback_target_index"] = int(fallback)
            await self._push_event(parsed)

    async def resize_pty(self, *, cols: int, rows: int) -> bool:
        async with self._lock:
            if self.session is None:
                return False
            return bool(await self.session.resize(cols=cols, rows=rows))

    async def _start_session_locked(
        self,
        *,
        command_override: Optional[List[str]] = None,
        resume_flags: Optional[List[str]] = None,
    ) -> None:
        cmd, cwd = _build_runtime_command(command_override=command_override, resume_flags=resume_flags)
        self.session = ConPTYSession(cmd=cmd, cwd=cwd, emit=self._emit)
        started = await self.session.start()
        if not started:
            raise RuntimeError("ConPTY session failed to start.")
        self._initial_input_anchor_created = False
        self._stdin_commit_buffers = {}
        self._wait_task = asyncio.create_task(self.session.wait())
        anchor_page = self._anchors_catalog(limit=self.anchor_page_size, offset=0)
        await self._push_event(
            {
                "type": "ui_state",
                "update": "init",
                "stages": self.intent.stage_payload(),
                "actions": self.protocol.get("actions") if isinstance(self.protocol.get("actions"), list) else [],
                "intent_rule_count": len(self.intent.intent_rules),
                "current_stage_index": self.intent.current_stage_index,
                "progress": 0,
                "anchors": anchor_page.get("anchors", []),
                "total_anchor_count": int(anchor_page.get("total_anchor_count", 0)),
                "has_more_anchors": bool(anchor_page.get("has_more", False)),
                "next_anchor_offset": int(anchor_page.get("next_offset", 0)),
                "anchor_page_size": int(anchor_page.get("limit", self.anchor_page_size)),
                "current_run_id": self.run_id,
                "startup_archive_info": dict(self.startup_archive_info),
            }
        )

    async def start(self) -> None:
        async with self._lock:
            self._is_replaying = False
            self._replay_auto_confirm_enabled = False
            await self._start_session_locked()

    async def stop(self) -> None:
        async with self._lock:
            self._is_replaying = False
            self._replay_auto_confirm_enabled = False
            self._stdin_commit_buffers = {}
            if self.session is not None:
                await self.session.stop()
            self.session = None
            if self._wait_task is not None and not self._wait_task.done():
                self._wait_task.cancel()
            self._wait_task = None

    async def wait_for_exit(self) -> int:
        if self._wait_task is None:
            return 1
        try:
            return int(await self._wait_task)
        except asyncio.CancelledError:
            return 1

    async def send_stdin(
        self,
        text: str,
        *,
        record: bool = True,
        source: str = "user",
        input_type: str = "text",
    ) -> None:
        if self.session is None:
            raise RuntimeError("Session is not running.")
        raw = str(text or "")
        if not raw:
            return
        had_ansi = _contains_ansi(raw)
        clean = _strip_ansi(raw)
        if had_ansi and not clean.strip():
            await self._push_event(
                {
                    "type": "warn",
                    "message": "Blocked ANSI-only stdin payload.",
                    "source": source,
                }
            )
            return
        if not clean:
            return
        if had_ansi:
            await self._push_event(
                {
                    "type": "warn",
                    "message": "Filtered ANSI control sequence from stdin payload.",
                    "source": source,
                }
            )
        if source == "replay":
            # Keep replay inputs line-oriented to reduce echo interleaving.
            self._replay_auto_confirm_enabled = True
            if not clean.endswith(("\n", "\r")):
                clean = clean + "\n"
        norm_input_type = "password" if str(input_type or "").lower() == "password" else "text"
        await self.session.send_stdin(clean)
        committed_inputs: List[str] = []
        if record:
            committed_inputs = self._consume_committed_inputs(source, clean)
            for committed in committed_inputs:
                self.input_records.append(
                    {
                        "text": committed,
                        "stage_index": int(self.intent.current_stage_index),
                        "source": source,
                        "input_type": norm_input_type,
                        "ts": _now_iso(),
                    }
                )
        # First anchor is created only after the first user input is completed (Enter/newline committed).
        if (
            record
            and source == "user"
            and committed_inputs
            and not self._initial_input_anchor_created
        ):
            stage_index = int(self.intent.current_stage_index)
            if stage_index < 0:
                stage_index = 0
            stage_id = ""
            stage_label = f"stage_{stage_index + 1}"
            if 0 <= stage_index < len(self.intent.stages):
                stage_meta = self.intent.stages[stage_index]
                stage_id = str(stage_meta.get("id") or "")
                stage_label = str(stage_meta.get("label") or stage_label)
            await self._create_anchor_event(
                stage_index=stage_index,
                stage_id=stage_id,
                stage_label=stage_label,
                reason="initial_input",
            )
            self._initial_input_anchor_created = True
        if source != "replay":
            await self._push_event(
                {
                    "type": "stdio",
                    "stream": "user_input",
                    "line": clean,
                    "source": source,
                    "input_type": norm_input_type,
                }
            )

    async def rollback_to_stage(self, target_stage_index: int) -> Dict[str, Any]:
        async with self._lock:
            stage_count = len(self.intent.stages)
            if stage_count <= 0:
                raise RuntimeError("Protocol has no stages.")

            target = int(target_stage_index)
            if target < 0:
                target = 0
            if target >= stage_count:
                target = stage_count - 1
            resume_plan = self._target_stage_resume_plan(target)

            self._is_replaying = True
            self._replay_auto_confirm_enabled = False
            self._last_stdout_emit_ts = time.monotonic()

            await self._push_event(
                {
                    "type": "ui_state",
                    "update": "rollback_start",
                    "reason": "rollback_start",
                    "target_stage_index": target,
                    "_allow_during_replay_visible": True,
                }
            )
            await self._push_event(
                {
                    "type": "ui_state",
                    "update": "rollback",
                    "phase": "start",
                    "target_stage_index": target,
                }
            )

            source_anchor = self._find_anchor_for_target(target)
            replay_records: List[Dict[str, Any]] = []
            skipped_ansi_only = 0
            if not bool(resume_plan.get("use_resume")):
                if source_anchor is not None:
                    replay_records = list(source_anchor.get("last_inputs") or [])
                else:
                    replay_records = [r for r in self.input_records if int(r.get("stage_index", -1)) < target]

                replay_clean: List[Dict[str, Any]] = []
                for rec in replay_records:
                    raw_text = str(rec.get("text") or "")
                    cleaned_text = _strip_ansi(raw_text)
                    if not cleaned_text.strip():
                        if _contains_ansi(raw_text):
                            skipped_ansi_only += 1
                        continue
                    next_rec = dict(rec)
                    next_rec["text"] = cleaned_text
                    replay_clean.append(next_rec)
                replay_records = replay_clean

            if self.session is not None:
                await self.session.stop()
            self.session = None
            if self._wait_task is not None and not self._wait_task.done():
                self._wait_task.cancel()
            self._wait_task = None

            if source_anchor is not None and self.snapshot_enabled:
                restore_anchor_id = str(source_anchor.get("anchor_id") or "").strip()
                if restore_anchor_id:
                    try:
                        await asyncio.to_thread(self._restore_physical_snapshot, restore_anchor_id)
                    except Exception as exc:
                        await self._push_event(
                            {
                                "type": "warn",
                                "message": f"Workspace snapshot restore failed for {restore_anchor_id}: {exc}",
                            }
                        )

            await self._push_event(
                {
                    "type": "ui_state",
                    "update": "replay_mode",
                    "active": True,
                    "target_stage_index": target,
                }
            )
            try:
                self.intent.set_stage(-1)
                if bool(resume_plan.get("use_resume")):
                    await self._start_session_locked(
                        command_override=resume_plan.get("command_override"),
                        resume_flags=resume_plan.get("resume_flags"),
                    )
                    await asyncio.sleep(1.0)
                    await self.wait_for_quiet(quiet_ms=800, max_wait_sec=3.0)
                else:
                    self._replay_auto_confirm_enabled = True
                    self._last_auto_confirm_sig = ""
                    self._last_auto_confirm_ts = 0.0
                    await self._start_session_locked()
                    await asyncio.sleep(1.0)
                    await self.wait_for_quiet(quiet_ms=800, max_wait_sec=3.0)
                    await self._push_event(
                        {
                            "type": "stdio",
                            "stream": "stdout",
                            "chunk": "\r\n",
                            "_allow_during_replay_visible": True,
                        }
                    )
                    await self._push_event(
                        {
                            "type": "stdio",
                            "stream": "stdout",
                            "chunk": "--- [Rollback] Reconstructing Timeline ---",
                            "_allow_during_replay_visible": True,
                        }
                    )
                    await self._push_event(
                        {
                            "type": "stdio",
                            "stream": "stdout",
                            "chunk": "\r\n",
                            "_allow_during_replay_visible": True,
                        }
                    )
                    await asyncio.sleep(1.0)

                    if skipped_ansi_only > 0:
                        await self._push_event(
                            {
                                "type": "warn",
                                "message": f"Skipped {skipped_ansi_only} ANSI-only replay input(s) during rollback.",
                            }
                        )

                    for rec in replay_records:
                        await self.send_stdin(
                            str(rec.get("text") or ""),
                            record=False,
                            source="replay",
                            input_type=str(rec.get("input_type") or "text"),
                        )
                        await asyncio.sleep(0.1)
                    await asyncio.sleep(1.0)
                    await self.wait_for_quiet(quiet_ms=1000, max_wait_sec=4.0)

                sync_stage_index = int(target)
                if sync_stage_index == 0:
                    target_progress = 0.0
                elif source_anchor is not None and int(source_anchor.get("stage_index", -1)) <= 0:
                    sync_stage_index = 0
                    target_progress = 0.0
                else:
                    target_progress = self._progress_for_stage(sync_stage_index)
                self.intent.set_stage(sync_stage_index)

                target_stage_label = f"stage_{sync_stage_index + 1}"
                if 0 <= sync_stage_index < len(self.intent.stages):
                    target_stage_label = str(self.intent.stages[sync_stage_index].get("label") or target_stage_label)
                await self._push_event(
                    {
                        "type": "ui_state",
                        "update": "rollback_sync",
                        "current_stage_index": sync_stage_index,
                        "target_stage_index": target,
                        "progress": target_progress,
                        "current_stage_label": target_stage_label,
                        "rollback_mode": str(resume_plan.get("mode") or "replay"),
                        "_allow_during_replay_visible": True,
                    }
                )
                await self._push_event(
                    {
                        "type": "ui_state",
                        "update": "sync_after_rollback",
                        "current_stage_index": sync_stage_index,
                        "target_stage_index": target,
                        "progress": target_progress,
                        "current_stage_label": target_stage_label,
                        "rollback_mode": str(resume_plan.get("mode") or "replay"),
                        "_allow_during_replay_visible": True,
                    }
                )
                payload = {
                    "type": "ui_state",
                    "update": "rollback_complete",
                    "current_stage_index": sync_stage_index,
                    "target_stage_index": target,
                    "progress": target_progress,
                    "current_stage_label": target_stage_label,
                    "replayed_count": len(replay_records),
                    "rollback_mode": str(resume_plan.get("mode") or "replay"),
                    "source_anchor_id": str(source_anchor.get("anchor_id") or "") if source_anchor else "",
                    "source_anchor_stage_index": int(source_anchor.get("stage_index", -1)) if source_anchor else -1,
                    "source_anchor_run_id": str(source_anchor.get("run_id") or "") if source_anchor else "",
                    "message": (
                        f"Rolled back to stage {target + 1} via {str(resume_plan.get('mode') or 'replay')}, "
                        f"replayed {len(replay_records)} recorded inputs."
                    ),
                }
                await self._push_event(payload)
                self._is_replaying = False
                self._replay_auto_confirm_enabled = False
                await self._push_event(
                    {
                        "type": "ui_state",
                        "update": "replay_mode",
                        "active": False,
                        "target_stage_index": target,
                    }
                )
                return payload
            finally:
                if self._is_replaying:
                    self._is_replaying = False
                    self._replay_auto_confirm_enabled = False
                    await self._push_event(
                        {
                            "type": "ui_state",
                            "update": "replay_mode",
                            "active": False,
                            "target_stage_index": target,
                        }
                    )

    async def rollback_to_anchor(self, anchor_id: str) -> Dict[str, Any]:
        anchor = self._find_anchor_by_id(anchor_id)
        if anchor is None:
            raise RuntimeError(f"Anchor not found: {anchor_id}")
        target_stage_index = int(anchor.get("stage_index", 0)) + 1
        if target_stage_index < 0:
            target_stage_index = 0
        payload = await self.rollback_to_stage(target_stage_index)
        payload["requested_anchor_id"] = str(anchor.get("anchor_id") or "")
        await self._push_event(
            {
                "type": "ui_state",
                "update": "anchor_rollback",
                "requested_anchor_id": str(anchor.get("anchor_id") or ""),
                "target_stage_index": int(payload.get("target_stage_index", target_stage_index)),
                "progress": self._progress_for_stage(int(payload.get("target_stage_index", target_stage_index))),
                "replayed_count": int(payload.get("replayed_count", 0)),
            }
        )
        return payload

    async def emit_anchors_catalog(
        self,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
        append: bool = False,
    ) -> Dict[str, Any]:
        page = self._anchors_catalog(limit=limit, offset=offset)
        payload = {
            "type": "ui_state",
            "update": "anchors_catalog",
            "anchors": page.get("anchors", []),
            "anchor_count": int(page.get("total_anchor_count", len(self.anchor_snapshots))),
            "total_anchor_count": int(page.get("total_anchor_count", len(self.anchor_snapshots))),
            "has_more": bool(page.get("has_more", False)),
            "next_offset": int(page.get("next_offset", 0)),
            "offset": int(page.get("offset", 0)),
            "limit": int(page.get("limit", self.anchor_page_size)),
            "append": bool(append),
            "current_run_id": str(page.get("current_run_id") or self.run_id),
        }
        await self._push_event(payload)
        return payload

    async def clear_history_anchors(self) -> Dict[str, Any]:
        async with self._lock:
            before = len(self.anchor_snapshots)
            kept = [a for a in self.anchor_snapshots if str(a.get("run_id") or "") == self.run_id]
            self.anchor_snapshots = kept
            cleared = max(0, before - len(kept))
            self._persist_all_anchors_to_active_log()
            deleted_legacy_files = self._clear_legacy_backup_files()
            self._recompute_anchor_seq()
            page = self._anchors_catalog(limit=self.anchor_page_size, offset=0)
            payload = {
                "type": "ui_state",
                "update": "anchors_history_cleared",
                "cleared_count": cleared,
                "deleted_legacy_files": deleted_legacy_files,
                "anchors": page.get("anchors", []),
                "total_anchor_count": int(page.get("total_anchor_count", len(self.anchor_snapshots))),
                "has_more": bool(page.get("has_more", False)),
                "next_offset": int(page.get("next_offset", 0)),
                "limit": int(page.get("limit", self.anchor_page_size)),
                "offset": int(page.get("offset", 0)),
                "current_run_id": str(page.get("current_run_id") or self.run_id),
            }
            await self._push_event(payload)
            return payload

    async def auto_recover(self, prompt: str = "", target_stage_index: Optional[int] = None) -> Dict[str, Any]:
        target = self.last_success_stage_index
        if target_stage_index is not None:
            try:
                target = int(target_stage_index)
            except Exception:
                pass
        if target < 0:
            target = max(0, int(self.intent.current_stage_index) - 1)

        rollback_payload = await self.rollback_to_stage(target)
        clean_prompt = str(prompt or "").strip()
        if clean_prompt:
            await self.send_stdin(clean_prompt + "\n", record=True, source="auto_prompt")

        done = {
            "type": "ui_state",
            "update": "auto_recover_done",
            "current_stage_index": int(rollback_payload.get("current_stage_index", target)),
            "target_stage_index": int(rollback_payload.get("target_stage_index", target)),
            "progress": self._progress_for_stage(int(rollback_payload.get("target_stage_index", target))),
            "replayed_count": int(rollback_payload.get("replayed_count", 0)),
            "prompt_sent": bool(clean_prompt),
            "message": "Auto recovery completed."
            if not clean_prompt
            else "Auto recovery completed and new prompt sent.",
        }
        await self._push_event(done)
        return done

    async def pump_events(self, ws: WebSocket) -> None:
        while True:
            event = await self.events.get()
            await ws.send_json(event)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "ts": _now_iso()}


@app.get("/")
async def index() -> Any:
    if INDEX_PATH.exists():
        return FileResponse(str(INDEX_PATH))
    return JSONResponse({"ok": True, "message": "AIUI WebSocket runner is ready.", "ws": "/ws/bridge"})


async def _receive_json_or_text(ws: WebSocket) -> Dict[str, Any]:
    frame = await ws.receive()
    ftype = frame.get("type")
    if ftype == "websocket.disconnect":
        raise WebSocketDisconnect(code=int(frame.get("code", 1000)))

    raw_text: Optional[str] = None
    if frame.get("text") is not None:
        raw_text = str(frame["text"])
    elif frame.get("bytes") is not None:
        raw_text = bytes(frame["bytes"]).decode("utf-8", errors="replace")

    if raw_text is None:
        return {"type": "noop"}

    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"type": "stdin", "payload": raw_text}


@app.websocket("/ws/bridge")
async def ws_bridge(ws: WebSocket) -> None:
    await ws.accept()
    bridge = LiveBridge()
    sender_task: Optional[asyncio.Task[Any]] = None
    try:
        await bridge.start()
        sender_task = asyncio.create_task(bridge.pump_events(ws))
        await ws.send_json({"type": "lifecycle", "event": "ws_connected", "ts": _now_iso()})

        while True:
            result = await _receive_json_or_text(ws)
            msg_type = str(result.get("type") or "").strip().lower()
            if msg_type == "stdin":
                text = str(result.get("payload") or result.get("text") or result.get("data") or "")
                input_type = str(result.get("input_type") or "text")
                if text:
                    await bridge.send_stdin(text, record=True, source="user", input_type=input_type)
            elif msg_type == "resize":
                cols = _safe_int(result.get("cols"), 140)
                rows = _safe_int(result.get("rows"), 36)
                await bridge.resize_pty(cols=cols, rows=rows)
            elif msg_type in {"list_anchors", "anchors", "get_anchors"}:
                raw_limit = result.get("limit", result.get("page_size"))
                raw_offset = result.get("offset", 0)
                page_size = _safe_int(raw_limit, bridge.anchor_page_size)
                page_size = max(1, min(_ANCHOR_MAX_PAGE_SIZE, page_size))
                offset = max(0, _safe_int(raw_offset, 0))
                append = bool(result.get("append", False))
                await bridge.emit_anchors_catalog(limit=page_size, offset=offset, append=append)
            elif msg_type in {"clear_anchor_history", "clear_history_anchors", "anchors_clear_history"}:
                await bridge.clear_history_anchors()
            elif msg_type in {"rollback_stage", "rollback", "rewind"}:
                raw_target = result.get("target_stage_index", result.get("index", 0))
                try:
                    target = int(raw_target)
                except Exception:
                    target = 0
                await bridge.rollback_to_stage(target)
            elif msg_type in {"rollback_anchor", "rewind_anchor"}:
                anchor_id = str(result.get("anchor_id") or "")
                if not anchor_id:
                    await ws.send_json(
                        {
                            "type": "warn",
                            "message": "anchor_id is required for rollback_anchor",
                            "ts": _now_iso(),
                        }
                    )
                    continue
                await bridge.rollback_to_anchor(anchor_id)
            elif msg_type in {"auto_recover", "ai_diagnose_recover"}:
                raw_target = result.get("target_stage_index")
                prompt = str(result.get("prompt") or result.get("new_prompt") or "")
                target: Optional[int] = None
                if raw_target is not None:
                    try:
                        target = int(raw_target)
                    except Exception:
                        target = None
                await bridge.auto_recover(prompt=prompt, target_stage_index=target)
            elif msg_type == "ping":
                await ws.send_json({"type": "pong", "ts": _now_iso()})
            elif msg_type == "noop":
                continue
            else:
                await ws.send_json(
                    {
                        "type": "warn",
                        "message": f"Unsupported message type: {msg_type or 'unknown'}",
                        "ts": _now_iso(),
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        if sender_task is not None:
            sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender_task
        await bridge.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AIUI real-time websocket dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"[AIUI-WS] serving on {url}")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
