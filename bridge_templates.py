from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "bridge_template_assets"


@lru_cache(maxsize=3)
def _load_template(name: str) -> str:
    template_path = _TEMPLATE_DIR / name
    if not template_path.exists():
        raise FileNotFoundError(f"Bridge template file not found: {template_path}")
    return template_path.read_text(encoding="utf-8")


def render_bridge_script() -> str:
    return _load_template("aui_bridge.py")


def render_embedded_ui_html() -> str:
    return _load_template("index.html")


def render_ui_runner_script() -> str:
    return _load_template("ui_runner.py")
