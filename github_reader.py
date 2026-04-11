from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
#本地项目的readme文件
#提供readme_text + repo_brief（仓库简报） 原料
_STARTUP_HINT_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "docker-compose.yml",
    "docker-compose.yaml",
    "makefile",
    "Pipfile",
}
_STARTUP_HINT_FILES_LOWER = {x.lower() for x in _STARTUP_HINT_FILES}

def _resolve_local_repo_path(repo_url: str) -> Path:
    """
    Support local repository inputs:
    - local://relative/or/absolute/path
    - plain relative/absolute path
    """
    raw = (repo_url or "").strip()
    if raw.lower().startswith("local://"):
        raw = raw[len("local://") :]

    if not raw:
        raise ValueError("Local repository path is empty.")

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError(f"Local repository directory does not exist: {p}")
    return p


def _read_text_best_effort(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return path.read_text(encoding="utf-8", errors="replace")


def _extract_startup_files_from_top_level(items: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("type") or "") != "file":
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        if name in _STARTUP_HINT_FILES or name.lower() in _STARTUP_HINT_FILES_LOWER:
            names.append(name)
    seen = set()
    out: List[str] = []
    for n in names:
        k = n.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


def _pick_local_doc_file(repo_path: Path) -> Optional[Path]:
    # Prefer README first. If not found, fallback to top-level markdown.
    preferred = [
        "README.md",
        "README.MD",
        "readme.md",
    ]
    for name in preferred:
        p = repo_path / name
        if p.exists() and p.is_file():
            return p
    markdown_files = sorted([p for p in repo_path.iterdir() if p.is_file() and p.suffix.lower() == ".md"], key=lambda x: x.name.lower())
    return markdown_files[0] if markdown_files else None


def _read_local_doc_text(repo_path: Path, max_chars: int) -> Tuple[str, str]:
    selected = _pick_local_doc_file(repo_path)
    if not selected:
        return "", ""
    doc_text = _read_text_best_effort(selected).strip()
    if len(doc_text) > max_chars:
        doc_text = doc_text[:max_chars] + "\n...(truncated)\n"
    return doc_text, selected.name


def _local_top_level_items(repo_path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for child in sorted(repo_path.iterdir(), key=lambda p: p.name.lower()):
        item: Dict[str, Any] = {
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "path": child.name,
        }
        if child.is_file():
            try:
                item["size"] = child.stat().st_size
            except Exception:
                pass
        items.append(item)
    return items


def _fetch_local_repo_context(repo_url: str, max_readme_chars: int) -> Dict[str, Any]:
    repo_path = _resolve_local_repo_path(repo_url)
    readme_text, readme_file = _read_local_doc_text(repo_path, max_chars=max_readme_chars)
    top_level_items = _local_top_level_items(repo_path)
    startup_files = _extract_startup_files_from_top_level(top_level_items)

    repo_brief = {
        "source_type": "local",
        "full_name": repo_path.name,
        "local_path": str(repo_path),
        "default_branch": "local",
        "description": f"Local repository at {repo_path}",
        "language": "",
        "readme_file": readme_file,
        "startup_files": startup_files,
        "top_level_items": top_level_items,
    }

    return {
        "repo_url": repo_url,
        "repo_brief": repo_brief,
        "readme_text": readme_text,
    }

def fetch_repo_context(repo_url: str, max_readme_chars: int = 8000) -> Dict[str, Any]:
    return _fetch_local_repo_context(repo_url, max_readme_chars=max_readme_chars)


