export type StepKind = "setup" | "analysis" | "execution" | "validation" | "cleanup" | "custom";

export type Step = {
  id: string;
  label: string;
  description: string;
  kind?: StepKind;
  inputs?: string[];
  outputs?: string[];
  next?: string | null;
  ui_hints?: Record<string, unknown> | null;
  ai_next_prompt?: string | null;
};

export type Plan = {
  version: number;
  repo_url: string;
  repo_brief: string;
  steps: Step[];
  created_at: string;
};

export type PlanResponse = {
  thread_id: string;
  plan: Plan;
  steps_json_path: string;
};

export type HistoryItem = {
  checkpoint_id: string | null;
  current_step_index: number | null;
  last_step_id: string | null;
  checkpoint_summary: string | null;
  replan_required: boolean;
};

export type AnchorsOverviewResponse = {
  thread_id: string;
  checkpoint_id: string | null;
  state_source?: "graph" | "timeline" | string;
  total_steps: number;
  completed_steps: number;
  progress_percent: number;
  current_step_index: number | null;
  current_step: Step | null;
  next_step: Step | null;
  last_step_id: string | null;
  anchors: HistoryItem[];
};

export type RunRequest = {
  repo_url: string;
  user_notes: string;
  thread_id?: string;
  thread_name?: string;
  max_steps?: number;
};

export type RunResponse = {
  thread_id: string;
  plan_size: number;
  current_step_index: number | null;
  last_step_id: string | null;
  steps_completed_this_call: number;
  workplace?: Record<string, string>;
  ui_artifacts?: {
    ui_dir: string;
    latest_ui_path: string;
    timeline_path: string;
  };
};

export type RollbackRequest = {
  thread_id: string;
  checkpoint_id?: string | null;
  step_id?: string | null;
  user_notes: string;
  context_ui?: UIModificationContext | Record<string, unknown> | null;
  image_url?: string | null;
};

export type UIModificationScope = "content" | "style" | "layout" | "behavior";
export type UIModificationContext = {
  target_id: string;
  component_type: string;
  original_spec: Record<string, unknown>;
  user_intent: string;
  scope: UIModificationScope;
};

export type ThreadStateValues = {
  repo_url?: string;
  user_notes?: string;
  plan?: Step[];
  current_step_index?: number;
  last_step_id?: string | null;
  step_outputs?: Record<string, { checkpoint_summary?: string; ui_render?: UIRender }>;
  replan_required?: boolean;
  context_ui?: UIModificationContext | null;
};

export type ThreadStateResponse = {
  thread_id: string;
  checkpoint_id: string | null;
  state_source?: "graph" | "timeline" | string;
  values: ThreadStateValues;
};

export type ExportUIRequest = {
  target_dir?: string;
  target_project_root?: string;
  mode?: "copy" | "sync";
};

export type ExportIntegrationInfo = {
  target_project_root: string;
  integration_ui_dir: string;
  manifest_path: string;
  entry_file_path: string;
  readme_path: string;
};

export type ExportUIResponse = {
  thread_id: string;
  mode: "copy" | "sync";
  source_ui_dir: string;
  target_dir: string;
  exported_files: string[];
  integration?: ExportIntegrationInfo;
};

export type BridgeStage = {
  id: string;
  label: string;
  description?: string;
  anchor_name?: string;
  match_patterns?: string[];
  match_rules?: Array<Record<string, unknown>>;
};

export type BridgeControlType = "text" | "number" | "switch";

export type BridgeControl = {
  id: string;
  label: string;
  type: BridgeControlType;
  flag: string;
  default?: unknown;
  source?: string;
};

export type BridgePreview = {
  version: number;
  repo_url: string;
  core_functionality: string;
  execution_flow: string[];
  analysis_source: {
    repo_name: string;
    default_branch: string;
    readme_chars: number;
    source_type?: string;
    readme_file?: string;
    startup_files?: string[];
    top_level_items_count: number;
    user_notes: string;
  };
  launch_profile: {
    recommended_command: string;
    candidate_commands: string[];
    command_base: string[];
    passthrough_args: string[];
    flag_defaults: Record<string, unknown>;
    working_dir: string;
  };
  ui_protocol: {
    version: number;
    is_layered?: boolean;
    anchor_settings?: {
      dedupe_window_seconds?: number;
      similarity_threshold?: number;
      naming?: string;
    };
    status_bar: {
      stages: BridgeStage[];
    };
    controls: BridgeControl[];
    launch: {
      command: string;
      working_dir: string;
      supports_stdin: boolean;
    };
  };
};

export type BridgePreviewResponse = {
  thread_id?: string;
  preview_path?: string;
  preview: BridgePreview;
};

export type GenerateBridgeRequest = {
  target_project_root?: string;
  override_launch_command?: string;
  sync_logic_only?: boolean;
};

export type GenerateBridgeResponse = {
  thread_id: string;
  target_project_root: string;
  target_bridge_dir: string;
  preview_path?: string;
  operation_mode?: "full_generate" | "sync_logic_only" | string;
  protocol_preserved?: boolean;
  target_files: Record<string, string>;
  workplace_files: Record<string, string>;
};

export type SidecarStatusResponse = {
  thread_id: string;
  target_project_root: string;
  sidecar_root: string;
  sidecar_exists: boolean;
  bridge_exists: boolean;
  ui_exists: boolean;
  protocol_exists: boolean;
};

export type RescanBridgeRequest = {
  target_project_root?: string;
  override_launch_command?: string;
};

export type RescanBridgeResponse = {
  thread_id: string;
  repo_source: string;
  target_project_root: string;
  target_bridge_dir: string;
  preview_path?: string;
  target_files: Record<string, string>;
  preview: BridgePreview;
};

/**
 * 后端返回的低成本 UI Schema。
 *
 * 这里刻意保持宽松：前端用解释器（runtime guards）把 unknown content 渲染成组件。
 * 这样既支持已知类型（info_card/terminal/form），也支持未来 AI 动态新增的 type。
 */
export type UIRender = {
  type: string;
  content: unknown;
};

