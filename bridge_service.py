from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from bridge_generator import apply_launch_command_override, build_bridge_preview
from github_reader import fetch_repo_context


def _resolve_repo_context(repo_source: str, repo_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return repo_ctx if repo_ctx is not None else fetch_repo_context(repo_source)


def _local_project_root_from_context(repo_ctx: Dict[str, Any]) -> Optional[Path]:
    brief = repo_ctx.get("repo_brief") or {}
    if str(brief.get("source_type") or "") != "local":
        return None
    local_path = (brief.get("local_path") or "").strip()
    if not local_path:
        return None
    return Path(local_path).resolve()


def normalize_repo_source(repo_url: Optional[str], repo_source: Optional[str]) -> str:
    candidate = (repo_source or "").strip() or (repo_url or "").strip()
    if not candidate:
        raise ValueError("repo_source/repo_url cannot be empty.")
    return candidate

def resolve_local_project_root_from_repo_source(
    repo_source: str,
    repo_ctx: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    ctx = _resolve_repo_context(repo_source, repo_ctx=repo_ctx)
    return _local_project_root_from_context(ctx)


def build_preview_from_source(
    *,
    repo_source: str,
    user_notes: str,
    plan_steps: List[Dict[str, Any]],
    override_launch_command: Optional[str] = None,
    repo_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_ctx = _resolve_repo_context(repo_source, repo_ctx=repo_ctx)
    preview = build_bridge_preview(
        repo_url=repo_source,
        user_notes=user_notes,
        plan_steps=plan_steps,
        repo_ctx=resolved_ctx,
    )
    if (override_launch_command or "").strip():
        preview = apply_launch_command_override(preview, (override_launch_command or "").strip())
    return preview
