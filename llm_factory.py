from __future__ import annotations

import os
from typing import Optional

from langchain.chat_models import init_chat_model
from langchain_openai import ChatOpenAI


def _first_non_empty(*values: Optional[str]) -> str:
    for v in values:
        if v and v.strip():
            return v.strip()
    return ""


def _bootstrap_openai_compatible_env() -> None:
    """
    Compatibility bridge:
    - If OPENAI_* is missing, reuse CODEX_* for OpenAI-compatible endpoints.
    """
    if not os.getenv("OPENAI_API_KEY"):
        codex_api_key = os.getenv("CODEX_API_KEY")
        if codex_api_key:
            os.environ["OPENAI_API_KEY"] = codex_api_key

    if not os.getenv("OPENAI_BASE_URL"):
        codex_base_url = os.getenv("CODEX_BASE_URL")
        if codex_base_url:
            os.environ["OPENAI_BASE_URL"] = codex_base_url
    if not os.getenv("OPENAI_API_BASE"):
        openai_base = os.getenv("OPENAI_BASE_URL") or os.getenv("CODEX_BASE_URL")
        if openai_base:
            os.environ["OPENAI_API_BASE"] = openai_base


def resolve_model_name() -> str:
    model_name = _first_non_empty(
        os.getenv("LLM_MODEL"),
        os.getenv("CODEX_MODEL"),
    )
    # Keep a deterministic fallback for local smoke tests.
    return model_name or "openai:gpt-5.3-codex"


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_openai_routed(model_name: str) -> bool:
    provider, _resolved = _split_model_ref(model_name)
    if provider == "openai":
        return True
    if provider:
        return False
    # No explicit provider prefix but OpenAI-compatible credentials exist.
    return bool(
        os.getenv("OPENAI_API_KEY")
        or os.getenv("CODEX_API_KEY")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("CODEX_BASE_URL")
    )


def _split_model_ref(model_name: str) -> tuple[str, str]:
    text = (model_name or "").strip()
    if ":" in text:
        provider, model = text.split(":", 1)
        return provider.strip().lower(), model.strip()
    return "", text


def _normalize_openai_base_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return normalized
    if normalized.endswith("/v1"):
        return normalized
    return normalized + "/v1"


def get_chat_llm():
    _bootstrap_openai_compatible_env()
    model_name = resolve_model_name()
    provider, resolved_model = _split_model_ref(model_name)

    if _is_openai_routed(model_name):
        model = resolved_model or model_name
        use_responses_api = _parse_bool_env("OPENAI_USE_RESPONSES_API", default=False)
        api_key = _first_non_empty(os.getenv("OPENAI_API_KEY"), os.getenv("CODEX_API_KEY"))
        base_url = _first_non_empty(
            os.getenv("OPENAI_API_BASE"),
            os.getenv("OPENAI_BASE_URL"),
            os.getenv("CODEX_BASE_URL"),
        )
        compat_user_agent = _first_non_empty(
            os.getenv("OPENAI_COMPAT_USER_AGENT"),
            os.getenv("CODEX_COMPAT_USER_AGENT"),
        )
        normalized_base = _normalize_openai_base_url(base_url) if base_url else ""
        if not compat_user_agent and "code.newcli.com" in normalized_base.lower():
            compat_user_agent = "curl/8.5.0"
        kwargs = {
            "model": model,
            "use_responses_api": use_responses_api,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = normalized_base
        if compat_user_agent:
            kwargs["default_headers"] = {"User-Agent": compat_user_agent}
        return ChatOpenAI(**kwargs)

    # Explicit provider route: keep provider prefix to avoid being misrouted by OPENAI/CODEX env vars.
    if provider and resolved_model:
        return init_chat_model(f"{provider}:{resolved_model}")

    return init_chat_model(model_name)
