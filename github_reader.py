from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


class GitHubRateLimitError(RuntimeError):
    pass


class GitHubAPIError(RuntimeError):
    pass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _is_github_style_url(value: str) -> bool:
    value = (value or "").strip()
    return value.startswith("http://github.com/") or value.startswith("https://github.com/") or value.startswith(
        "git@github.com:"
    ) or value.startswith("http://raw.githubusercontent.com/") or value.startswith(
        "https://raw.githubusercontent.com/"
    )


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


def _github_repo_from_url(repo_url: str) -> Tuple[str, str]:
    """
    Returns (owner, repo) from common GitHub URL formats.
    Supports:
      - https://github.com/{owner}/{repo}
      - https://github.com/{owner}/{repo}/...
      - git@github.com:{owner}/{repo}.git
      - https://raw.githubusercontent.com/{owner}/{repo}/...
    """
    repo_url = repo_url.strip()

    # git@github.com:owner/repo.git
    m = re.match(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", repo_url)
    if m:
        return m.group("owner"), m.group("repo")

    # https://github.com/owner/repo[/...]
    m = re.match(r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?(?:/.*)?$", repo_url)
    if m:
        return m.group("owner"), m.group("repo")

    # https://raw.githubusercontent.com/owner/repo/...
    m = re.match(
        r"^https?://raw\.githubusercontent\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?(?:/.*)?$",
        repo_url,
    )
    if m:
        return m.group("owner"), m.group("repo")

    raise ValueError(f"Unsupported GitHub repo url: {repo_url}")


def _http_get_json(url: str, token: Optional[str]) -> Any:
    headers = {
        "User-Agent": "ai-native-dashboard-planner",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        lowered = (str(e) + " " + body).lower()
        if e.code == 403 and ("rate limit" in lowered or "api rate limit exceeded" in lowered):
            raise GitHubRateLimitError("GitHub API rate limit exceeded") from e
        raise GitHubAPIError(f"GitHub API error {e.code}: {body or str(e)}") from e


def _http_get_text(url: str, token: Optional[str]) -> str:
    headers = {
        "User-Agent": "ai-native-dashboard-planner",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return raw
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        lowered = (str(e) + " " + body).lower()
        if e.code == 403 and ("rate limit" in lowered or "api rate limit exceeded" in lowered):
            raise GitHubRateLimitError("GitHub API rate limit exceeded") from e
        raise GitHubAPIError(f"GitHub API error {e.code}: {body or str(e)}") from e


def _fetch_readme_from_raw(owner: str, repo: str, max_readme_chars: int) -> str:
    # Raw content endpoint does not require GitHub REST API quota in the same way.
    branches = ["main", "master"]
    names = ["README.md", "readme.md", "Readme.md"]
    for branch in branches:
        for name in names:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{name}"
            try:
                text = _http_get_text(url, token=None)
                # Guard against raw 404 body.
                if text.strip() == "404: Not Found":
                    continue
                if len(text) > max_readme_chars:
                    text = text[:max_readme_chars] + "\n...(truncated)\n"
                return text
            except Exception:
                continue
    return ""


def _fallback_context_when_rate_limited(
    repo_url: str,
    owner: str,
    repo: str,
    max_readme_chars: int,
) -> Dict[str, Any]:
    readme_text = _fetch_readme_from_raw(owner, repo, max_readme_chars=max_readme_chars)
    repo_brief = {
        "full_name": f"{owner}/{repo}",
        "default_branch": "unknown",
        "description": "GitHub API rate-limited fallback context",
        "language": "",
        "top_level_items": [],
        "source_type": "github_rate_limited_fallback",
    }
    return {
        "repo_url": repo_url,
        "repo_brief": repo_brief,
        "readme_text": readme_text,
    }


def fetch_repo_context(repo_url: str, max_readme_chars: int = 8000) -> Dict[str, Any]:
    if not _is_github_style_url(repo_url):
        return _fetch_local_repo_context(repo_url, max_readme_chars=max_readme_chars)

    owner, repo = _github_repo_from_url(repo_url)
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("githubToken") or "").strip() or None
    allow_readme_fallback = _env_bool("GITHUB_ALLOW_README_FALLBACK", default=False)

    repo_api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        repo_info = _http_get_json(repo_api, token=token)
    except GitHubRateLimitError:
        if allow_readme_fallback:
            return _fallback_context_when_rate_limited(
                repo_url=repo_url,
                owner=owner,
                repo=repo,
                max_readme_chars=max_readme_chars,
            )
        raise
    default_branch = repo_info.get("default_branch", "main")
    full_name = repo_info.get("full_name", f"{owner}/{repo}")
    description = repo_info.get("description") or ""

    # README via /readme (returns base64 content)
    readme_api = f"https://api.github.com/repos/{owner}/{repo}/readme?ref={urllib.parse.quote(default_branch)}"
    readme_text = ""
    readme_file = ""
    try:
        readme_obj = _http_get_json(readme_api, token=token)
        content_b64 = readme_obj.get("content") or ""
        readme_file = str(readme_obj.get("path") or "")
        if content_b64:
            readme_text = base64.b64decode(content_b64).decode("utf-8", errors="replace")
    except Exception:
        # Fallback: attempt README.md directly via contents API.
        contents_api = f"https://api.github.com/repos/{owner}/{repo}/contents/README.md?ref={urllib.parse.quote(default_branch)}"
        try:
            readme_obj = _http_get_json(contents_api, token=token)
            content_b64 = readme_obj.get("content") or ""
            readme_file = str(readme_obj.get("path") or "README.md")
            if content_b64:
                readme_text = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception:
            readme_text = ""
            readme_file = ""

    if len(readme_text) > max_readme_chars:
        readme_text = readme_text[:max_readme_chars] + "\n...(truncated)\n"

    # Root directory listing (first level only)
    contents_api = f"https://api.github.com/repos/{owner}/{repo}/contents?ref={urllib.parse.quote(default_branch)}"
    top_level_items: List[Dict[str, Any]] = []
    try:
        items = _http_get_json(contents_api, token=token)
        if isinstance(items, list):
            for it in items:
                # Keep it small: name, type, and size if file
                item = {
                    "name": it.get("name"),
                    "type": it.get("type"),
                    "path": it.get("path"),
                }
                if it.get("type") == "file":
                    item["size"] = it.get("size")
                top_level_items.append(item)
    except Exception:
        top_level_items = []
    startup_files = _extract_startup_files_from_top_level(top_level_items)

    # A couple of extra “hints” for the planner.
    language_guess = repo_info.get("language") or ""

    repo_brief = {
        "full_name": full_name,
        "default_branch": default_branch,
        "description": description,
        "language": language_guess,
        "source_type": "github",
        "readme_file": readme_file,
        "startup_files": startup_files,
        "top_level_items": top_level_items,
    }

    return {
        "repo_url": repo_url,
        "repo_brief": repo_brief,
        "readme_text": readme_text,
    }

