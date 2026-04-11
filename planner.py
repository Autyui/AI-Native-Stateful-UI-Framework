from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict,Optional
from github_reader import fetch_repo_context
from llm_factory import get_chat_llm
from schemas import Plan, PlanRequest, Step


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_first_json_object(text: str) -> str:
    """
    Tries to extract the first top-level JSON object from an LLM response.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output.")
    return text[start : end + 1]


def _safe_parse_plan(raw: str) -> Plan:
    obj_text = _extract_first_json_object(raw)
    data = json.loads(obj_text)
    # Support both pydantic v1/v2 naming.
    if hasattr(Plan, "model_validate"):
        return Plan.model_validate(data)  # pydantic v2 style
    return Plan.parse_obj(data)  # type: ignore[attr-defined]


def _fallback_plan(repo_url: str) -> Plan:
    # Fallback is intentionally simple; later steps will refine it.
    created_at = _now_iso()
    steps = [
        Step(
            id="step_1",
            label="快速理解仓库",
            description="阅读 README 与目录结构，确认项目目标与关键入口文件。",
            kind="analysis",
            inputs=["repo_url"],
            outputs=["repo_brief", "key_files"],
            next="step_2",
        ),
        Step(
            id="step_2",
            label="规划执行路径",
            description="把项目任务拆成可检查的步骤，并为每一步定义预期产物。",
            kind="execution",
            inputs=["repo_brief"],
            outputs=["steps_json"],
            next="step_3",
        ),
        Step(
            id="step_3",
            label="运行与校验（可回溯）",
            description="执行到每一步后，提供锚点与回溯入口，确保用户可纠错。",
            kind="validation",
            inputs=["steps_json"],
            outputs=["checkpoint_state"],
            next=None,
        ),
    ]
    return Plan(version=1, repo_url=repo_url, repo_brief="(fallback)", steps=steps, created_at=created_at)


def _get_llm():
    return get_chat_llm()


def generate_steps_from_repo(req: PlanRequest,repo_ctx: Optional[Dict[str, Any]] = None) -> Plan:
    """
    MVP Step 1: Repository source (local path) -> steps for a status bar.
    """
    repo_ctx = repo_ctx if repo_ctx is not None else fetch_repo_context(req.repo_url)

    llm = _get_llm()

    system_prompt = (
        "你是“项目任务分解器”。"
        "你的任务是把一个代码仓库（本地目录）的目标拆成 4-8 个步骤，用于前端状态栏展示。"
        "你必须只输出一个 JSON 对象，且该 JSON 必须符合我提供的 schema（禁止输出任何额外文字）。\n"
        "schema:\n"
        "{\n"
        '  "version": 1,\n'
        '  "repo_url": string,\n'
        '  "repo_brief": string,\n'
        '  "steps": [\n'
        "    {\n"
        '      "id": "step_1" | "step_2" | ... (字符串),\n'
        '      "label": string,       // 状态栏展示名（中文更好）\n'
        '      "description": string, // 这一步要做什么\n'
        '      "kind": "setup" | "analysis" | "execution" | "validation" | "cleanup" | "custom",\n'
        '      "inputs": string[],\n'
        '      "outputs": string[],\n'
        '      "next": string|null,    // 下一个 step id\n'
        '      "ui_hints": object|null,// UI 提示（可选，用于按钮/输入框等）\n'
        '      "ai_next_prompt": string|null // 下一步执行用的提示片段（可选）\n'
        "    }\n"
        "  ],\n"
        '  "created_at": string // ISO8601\n'
        "}\n"
    )

    user_notes = (req.user_notes or "").strip()
    human_prompt = {
        "repo_url": req.repo_url,
        "repo_brief": repo_ctx["repo_brief"],
        "readme_text": repo_ctx["readme_text"],
        "user_notes": user_notes,
        "constraints": {
            "step_count_range": [4, 8],
            "output_language_preference": "zh-CN",
            "checkpoint_friendly": True,
        },
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(human_prompt, ensure_ascii=False)},
    ]

    raw = llm.invoke(messages)
    raw_text = getattr(raw, "content", None) or str(raw)

    try:
        plan = _safe_parse_plan(raw_text)
        return plan
    except Exception:
        # As MVP we prefer returning something usable over failing hard.
        return _fallback_plan(req.repo_url)

