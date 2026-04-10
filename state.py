from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from llm_factory import get_chat_llm
from planner import generate_steps_from_repo
from schemas import PlanRequest


class UIModificationContextState(TypedDict, total=False):
    target_id: str
    component_type: str
    original_spec: Dict[str, Any]
    user_intent: str
    scope: str


class AgentState(TypedDict, total=False):
    """
    LangGraph state for AUI-Dashboard.

    锚点 A（方案 A）约定：
    当用户点击 UI 上的某个“步骤锚点”，回溯对应的是该步骤“刚完成”后的 checkpoint。
    因此：execute_current_step 节点执行完成后，last_step_id / last_step_index 会被写入 state，
    该 checkpoint 即可被 UI 作为锚点。
    """

    repo_url: str
    # 存储用户最新的指令（用于回溯后的重规划）
    user_notes: str

    # 从 planner 获取的步骤列表（每个元素为 dict，遵循 Step schema）
    plan: List[Dict[str, Any]]
    current_step_index: int

    # 记录刚完成的步骤 ID，用于锚点对齐（方案 A）
    last_step_id: Optional[str]

    # 存储每一步的执行结果/UI Schema
    step_outputs: Dict[str, Any]

    # 回溯后置为 True，触发 ensure_plan 重规划
    replan_required: bool
    # Structured contextual UI payload for component-level refinement.
    context_ui: Optional[UIModificationContextState]
    # Optional image data URL for multimodal rollback instructions.
    image_url: Optional[str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_first_json_object(text: str) -> str:
    raw = str(text or "")
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, flags=re.IGNORECASE)
    if fence:
        return fence.group(1)

    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output.")

    depth = 0
    end = -1
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = idx
                break
    if end <= start:
        raise ValueError("No complete JSON object found in model output.")
    return raw[start : end + 1]


def _parse_json_or_text(raw: str) -> Dict[str, Any]:
    """
    Best-effort parser.
    If the model returns JSON, use it; otherwise wrap raw content.
    """
    try:
        obj_text = _extract_first_json_object(raw)
        data = json.loads(obj_text)
        if isinstance(data, dict):
            return data
        return {"result_text": raw}
    except Exception:
        return {"result_text": raw}


def _coerce_model_text(raw: Any) -> str:
    content = getattr(raw, "content", raw)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
                continue
            if isinstance(item, dict):
                maybe = item.get("text") or item.get("content")
                if isinstance(maybe, str) and maybe.strip():
                    parts.append(maybe.strip())
        if parts:
            return "\n".join(parts).strip()
    return ""


def _default_ui_render_for_step(step: Dict[str, Any], step_id: str, summary: str, result_text: str) -> Dict[str, Any]:
    kind = str(step.get("kind") or "").strip().lower()
    title = str(step_id)
    label = str(step.get("label") or "").strip()
    if kind in {"setup", "execution"}:
        lines = [
            f"Step: {step_id}",
            f"Summary: {summary}",
        ]
        if label and label != step_id:
            lines.append(f"Label: {label}")
        if result_text:
            lines.append(result_text[:400])
        return {
            "type": "terminal",
            "content": {
                "title": f"Step Runtime - {title}",
                "lines": lines,
            },
        }
    if kind in {"analysis", "validation"}:
        return {
            "type": "form",
            "content": {
                "title": f"Step Review - {title}",
                "description": (f"Original label: {label}" if label and label != step_id else ""),
                "fields": [
                    {
                        "name": "refinement_prompt",
                        "label": "Refinement Prompt",
                        "kind": "textarea",
                        "placeholder": "Optional: describe what you want to refine for the next run.",
                    },
                    {
                        "name": "notes",
                        "label": "Operator Notes",
                        "kind": "textarea",
                        "placeholder": "Record observations and expected changes.",
                    },
                ],
            },
        }
    return {
        "type": "info_card",
        "content": {
            "title": f"Step Completed - {title}",
            "text": f"No detailed ui_render provided by model. ({step_id})",
            "items": [
                f"Checkpoint: {summary}",
                "Use rollback with optional prompt to refine.",
            ],
        },
    }


def _get_llm():
    return get_chat_llm()


def _completed_steps_context(state: AgentState) -> str:
    step_outputs = state.get("step_outputs") or {}
    if not step_outputs:
        return ""

    items: List[str] = []
    for step_id, out in step_outputs.items():
        if isinstance(out, dict):
            summary = out.get("checkpoint_summary") or out.get("result_text") or ""
            if isinstance(summary, str):
                summary = summary.strip().replace("\n", " ")
            items.append(f"- {step_id}: {summary[:220]}")
        else:
            items.append(f"- {step_id}: {str(out)[:220]}")

    return "\n".join(items)


def ensure_plan(state: AgentState) -> AgentState:
    # Decide whether we need to (re)plan.
    plan = state.get("plan") or []
    replan_required = bool(state.get("replan_required", False))

    if plan and not replan_required:
        return {}
    repo_url = state.get("repo_url")
    if not repo_url:
        raise ValueError("Missing repo_url in AgentState.")

    user_notes = (state.get("user_notes") or "").strip()
    context_ui = state.get("context_ui")
    has_context_ui = isinstance(context_ui, dict) and bool(context_ui)
    completed_ctx = _completed_steps_context(state)

    # If this is triggered by rollback, we append completed steps into the notes,
    # so planner.py can incorporate it. (MVP: without changing planner.py signature.)
    if replan_required and completed_ctx:
        user_notes = (
            f"{user_notes}\n\n"
            f"[AUI-Dashboard 回溯重规划上下文]\n"
            f"已完成步骤快照：\n{completed_ctx}\n\n"
            f"请在保留“已完成步骤”前缀意义的前提下，从最后一个已完成步骤之后继续，"
            f"并适当调整剩余步骤以匹配新的用户目标/偏好。"
        ).strip()
    if replan_required and has_context_ui:
        user_notes = (
            f"{user_notes}\n\n"
            f"检测到局部组件修改请求（context_ui={json.dumps(context_ui, ensure_ascii=False)}）。优先保持整体流程稳定，"
            "仅对目标组件相关步骤做最小必要调整，避免全局逻辑重写。"
        ).strip()

    req = PlanRequest(repo_url=repo_url, user_notes=user_notes or None)
    plan_obj = generate_steps_from_repo(req)

    # pydantic v1/v2 compatible dump.
    dumped_steps: List[Dict[str, Any]] = []
    for s in plan_obj.steps:
        if hasattr(s, "model_dump"):
            dumped_steps.append(s.model_dump())
        else:
            dumped_steps.append(s.dict())  # type: ignore[union-attr]

    # Start from after last completed step (方案 A锚点：last_step_id 对应“刚完成”那一步）。
    last_step_id = state.get("last_step_id")
    if last_step_id:
        next_index = 0
        for i, s in enumerate(dumped_steps):
            if s.get("id") == last_step_id:
                next_index = i + 1
                break
        current_step_index = next_index
    else:
        current_step_index = 0

    return {
        "plan": dumped_steps,
        "current_step_index": current_step_index,
        "replan_required": False,
    }


def execute_current_step(state: AgentState) -> AgentState:
    plan = state.get("plan") or []
    if not plan:
        return {}

    current_step_index = int(state.get("current_step_index") or 0)
    if current_step_index >= len(plan):
        return {}

    # 回溯时应先走 ensure_plan；此处加兜底避免执行旧步骤。
    if bool(state.get("replan_required", False)):
        return {}

    step = plan[current_step_index]
    step_id = step.get("id") or f"step_{current_step_index}"

    repo_url = state.get("repo_url") or ""
    user_notes = (state.get("user_notes") or "").strip()
    context_ui = state.get("context_ui")
    has_context_ui = isinstance(context_ui, dict) and bool(context_ui)
    image_url = state.get("image_url")
    ai_next_prompt = step.get("ai_next_prompt") or ""

    step_outputs = state.get("step_outputs") or {}
    completed_ctx = _completed_steps_context(state)

    llm = _get_llm()
    if has_context_ui:
        system_prompt = (
            "You are AUI-Dashboard UI refinement executor. "
            "You receive a normalized context_ui request. "
            "Apply minimal incremental UI change within the declared scope, keep target_id unchanged, "
            "and do not rewrite unrelated business logic or global flow. "
            "Return JSON only (no markdown, no explanation) in this format:\n"
            "{\n"
            '  "checkpoint_summary": string,\n'
            '  "ui_render": {\n'
            '    "type": string,\n'
            '    "content": object\n'
            "  },\n"
            '  "result_text": string,\n'
            '  "artifacts": object\n'
            "}\n"
        )
    else:
        system_prompt = (
            "You are AUI-Dashboard step executor. "
            "Generate checkpoint summary and practical UI schema for the current step. "
            "Prefer interactive component types (form/terminal) when appropriate. "
            "Return JSON only (no markdown, no explanation). Format:\n"
            "{\n"
            '  "checkpoint_summary": string,\n'
            '  "ui_render": {\n'
            '    "type": string,\n'
            '    "content": object\n'
            "  },\n"
            '  "result_text": string,\n'
            '  "artifacts": object\n'
            "}\n"
            "If uncertain, still provide a useful result_text and a valid ui_render."
        )

    user_prompt = {
        "repo_url": repo_url,
        "step": {
            "id": step_id,
            "label": step.get("label"),
            "description": step.get("description"),
            "kind": step.get("kind"),
            "inputs": step.get("inputs"),
            "outputs": step.get("outputs"),
            "ai_next_prompt": ai_next_prompt,
        },
        "user_notes": user_notes,
        "context_ui": context_ui,
        "image_url": image_url,
        "completed_steps_context": completed_ctx,
        "instructions": {
            "mvp": True,
            "remember": "此步骤执行完成后必须更新 checkpoint_summary 与 ui_render",
            "ui_render_requirement": "ui_render.type 必须可被前端渲染；ui_render.content 需包含与 type 对应的数据字段。",
            "multimodal_note": "如果 image_url 存在，请结合图片内容理解用户反馈；若模型不支持图像则忽略图片继续文本推理。",
        },
    }

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    # Try multimodal user input when image_url is provided; fallback to text-only below.
    if image_url:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": json.dumps(user_prompt, ensure_ascii=False)},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        )
    else:
        messages.append({"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)})

    try:
        raw = llm.invoke(messages)
    except Exception:
        # Provider/model may not support multimodal payload schema; fallback to text-only.
        raw = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
            ]
        )
    raw_text = _coerce_model_text(raw)
    if not raw_text:
        fallback_prompt = (
            f"{system_prompt}\n\n"
            f"Input JSON:\n{json.dumps(user_prompt, ensure_ascii=False)}\n\n"
            "Return JSON only."
        )
        try:
            raw_retry = llm.invoke(fallback_prompt)
            raw_text = _coerce_model_text(raw_retry)
        except Exception:
            raw_text = ""
    if not raw_text:
        raw_text = str(raw)
    parsed = _parse_json_or_text(raw_text)

    checkpoint_summary = parsed.get("checkpoint_summary") or f"completed:{step_id}"
    checkpoint_summary = str(checkpoint_summary).strip() or f"completed:{step_id}"
    ui_render = parsed.get("ui_render")
    if not isinstance(ui_render, dict):
        ui_render = _default_ui_render_for_step(step, step_id, checkpoint_summary, str(parsed.get("result_text") or ""))
    else:
        ui_type = str(ui_render.get("type") or "").strip()
        if not ui_type:
            ui_render["type"] = "info_card"
        if not isinstance(ui_render.get("content"), dict):
            ui_render["content"] = {
                "text": f"No detailed ui_render provided by model. ({step_id})",
            }

    result_text = parsed.get("result_text") or raw_text
    result_text = str(result_text)
    artifacts = parsed.get("artifacts") or {}

    # Persist step output into state.
    new_step_outputs = dict(step_outputs)
    new_step_outputs[step_id] = {
        "completed_at": _now_iso(),
        "checkpoint_summary": checkpoint_summary,
        "ui_render": ui_render,
        "result_text": result_text,
        "artifacts": artifacts,
    }

    # 此节点返回后的 checkpoint 即为“方案 A”锚点（刚完成第 i 步）。
    return {
        "step_outputs": new_step_outputs,
        "last_step_id": step_id,
        # After a successful refinement/execution write, clear contextual intent to avoid pollution.
        "context_ui": None,
    }


def advance_step(state: AgentState) -> AgentState:
    plan = state.get("plan") or []
    current_step_index = int(state.get("current_step_index") or 0)

    if bool(state.get("replan_required", False)):
        # 回溯重规划触发时不推进游标，确保 ensure_plan 根据 last_step_id 对齐起点。
        return {}

    if current_step_index >= len(plan) - 1:
        # Mark done by moving index beyond last.
        return {"current_step_index": len(plan), "context_ui": None, "image_url": None}

    # Once a new step begins, clear contextual refinement payload.
    return {"current_step_index": current_step_index + 1, "context_ui": None, "image_url": None}


def _route_after_start(state: AgentState) -> str:
    plan = state.get("plan") or []
    if not plan or bool(state.get("replan_required")):
        return "ensure_plan"
    return "execute_current_step"


def _route_after_advance(state: AgentState) -> str:
    plan = state.get("plan") or []
    current_step_index = int(state.get("current_step_index") or 0)
    if bool(state.get("replan_required", False)):
        return "ensure_plan"
    if current_step_index >= len(plan):
        return END
    return "execute_current_step"


# ================= 2. 构建核心 Graph =================
graph_builder = StateGraph(AgentState)
graph_builder.add_node("ensure_plan", ensure_plan)
graph_builder.add_node("execute_current_step", execute_current_step)
graph_builder.add_node("advance_step", advance_step)

graph_builder.add_conditional_edges(START, _route_after_start)
graph_builder.add_edge("ensure_plan", "execute_current_step")
graph_builder.add_edge("execute_current_step", "advance_step")
graph_builder.add_conditional_edges("advance_step", _route_after_advance)

memory = MemorySaver()
graph = graph_builder.compile(checkpointer=memory)


def get_state_history(thread_id: str):
    """
    Helper for app.py: fetch checkpoints for UI anchors & rollback.
    """
    config = {"configurable": {"thread_id": thread_id}}
    return list(graph.get_state_history(config))
