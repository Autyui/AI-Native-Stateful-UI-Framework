from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


StepKind = Literal[
    "setup",
    "analysis",
    "execution",
    "validation",
    "cleanup",
    "custom",
]

UIModificationScope = Literal["content", "style", "layout", "behavior"]


class Step(BaseModel):
    id: str = Field(..., description="Step unique id, used for checkpoints/links.")
    label: str = Field(..., description="Human readable name shown on status bar.")
    description: str = Field(..., description="What this step will do / why.")
    kind: StepKind = Field("custom", description="A coarse category for UI grouping.")
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    # Next step id. MVP can be sequential, but we keep it explicit for DAG extension.
    next: Optional[str] = None
    # UI hints are optional; front-end can ignore unknown fields.
    ui_hints: Optional[Dict[str, Any]] = None
    # For AI agents: hint prompt fragment to steer step execution.
    ai_next_prompt: Optional[str] = None


class Plan(BaseModel):
    version: int = 1
    repo_url: str
    repo_brief: str
    steps: List[Step]
    # Kept for deterministic UI and checkpoint mapping.
    created_at: str


class PlanRequest(BaseModel):
    repo_url: str
    user_notes: Optional[str] = None
    thread_id: Optional[str] = None


class PlanResponse(BaseModel):
    thread_id: str
    plan: Plan
    steps_json_path: str


class UIModificationContext(BaseModel):
    target_id: str = Field(..., description="Stable UI component target id.")
    component_type: str = Field(..., description="Component type, e.g. info_card/terminal/form.")
    original_spec: Dict[str, Any] = Field(default_factory=dict, description="Original component JSON spec.")
    user_intent: str = Field(..., description="User's natural language refinement intent.")
    scope: UIModificationScope = Field("content", description="Refinement scope boundary.")
