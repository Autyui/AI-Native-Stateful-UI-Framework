from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional

_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TASK_ID_RE = re.compile(r"^task_(\d+)(?:_.+)?$")


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def workplace_root() -> Path:
    return _project_root() / "workplace"


def ensure_workplace_root() -> Path:
    root = workplace_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_thread_id(thread_id: str) -> str:
    normalized = (thread_id or "").strip()
    if not _THREAD_ID_RE.fullmatch(normalized):
        raise ValueError(
            "Invalid thread_id. Allowed pattern: letters, numbers, underscore, dash; length 1-128."
        )
    return normalized


def _sanitize_label(label: str) -> str:
    raw = (label or "").strip().lower()
    if not raw:
        return ""
    # keep ascii letters/numbers/_/-, collapse other chars into underscores
    chars = []
    prev_is_sep = False
    for ch in raw:
        if ch.isascii() and (ch.isalnum() or ch in {"_", "-"}):
            chars.append(ch)
            prev_is_sep = False
        else:
            if not prev_is_sep:
                chars.append("_")
                prev_is_sep = True
    slug = "".join(chars).strip("_-")
    return slug[:48]


def generate_thread_id(thread_name: Optional[str] = None) -> str:
    """
    Generate a human-friendly thread id:
    - task_1
    - task_2_repo_ui
    """
    root = ensure_workplace_root()
    used = {p.name for p in root.iterdir() if p.is_dir()}

    max_num = 0
    for name in used:
        m = _TASK_ID_RE.fullmatch(name)
        if not m:
            continue
        max_num = max(max_num, int(m.group(1)))

    next_num = max_num + 1
    base = f"task_{next_num}"

    slug = _sanitize_label(thread_name or "")
    if slug:
        base = f"{base}_{slug}"

    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1

    return candidate


def ensure_thread_workplace(thread_id: str) -> Dict[str, str]:
    safe_thread_id = _validate_thread_id(thread_id)
    root = ensure_workplace_root()

    thread_root = root / safe_thread_id
    repo_dir = thread_root / "repo"
    ui_dir = thread_root / "ui"
    artifacts_dir = thread_root / "artifacts"
    logs_dir = thread_root / "logs"

    thread_root.mkdir(parents=True, exist_ok=True)
    repo_dir.mkdir(parents=True, exist_ok=True)
    ui_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    return {
        "thread_id": safe_thread_id,
        "workplace_root": str(root),
        "thread_root": str(thread_root),
        "repo_dir": str(repo_dir),
        "ui_dir": str(ui_dir),
        "artifacts_dir": str(artifacts_dir),
        "logs_dir": str(logs_dir),
    }


def workplace_config() -> Dict[str, str]:
    root = ensure_workplace_root()
    return {
        "mode": "fixed",
        "workplace_root": str(root),
        "thread_layout": "{workplace_root}/{thread_id}/{repo,ui,artifacts,logs}",
        "custom_workdir_enabled": "false",
    }
