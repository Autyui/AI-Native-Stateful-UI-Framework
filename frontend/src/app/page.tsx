"use client";

import * as React from "react";

import { ChronosBar } from "@/components/ChronosBar";
import { UIInterpreter } from "@/components/UIInterpreter";
import {
  ApiError,
  exportThreadUI,
  fetchThreadBridgePreview,
  fetchThreadSidecarStatus,
  fetchThreadState,
  generateThreadBridge,
  rescanThreadBridge,
  rollbackThread,
  runThread,
} from "@/lib/api";
import type { BridgePreview, HistoryItem, SidecarStatusResponse, ThreadStateResponse, UIModificationContext } from "@/lib/types";
type ContextUiPayload = {
  componentKey: string;
  componentType: string;
  componentJson: unknown;
  scope: "content" | "style" | "layout" | "behavior";
};
const DASHBOARD_BG_URL_STORAGE_KEY = "aiui-dashboard-bg-url";
const DASHBOARD_BG_OPACITY_STORAGE_KEY = "aiui-dashboard-bg-opacity";
const DEFAULT_DASHBOARD_BG_URL = process.env.NEXT_PUBLIC_AIUI_BG_URL || "http://localhost:8000/assets/bg-custom.jpg"; 

function isLikelyRemoteRepo(source: string) {
  return /^https?:\/\//i.test(source) || /^git@/i.test(source);
}

function normalizeLocalRepoPath(source: string) {
  const text = (source || "").trim();
  if (!text || isLikelyRemoteRepo(text)) return "";
  return text.startsWith("local://") ? text.slice("local://".length) : text;
}

function Toast({ message }: { message: string }) {
  return (
    <div className="fixed right-4 top-4 z-50 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 shadow-lg">
      {message}
    </div>
  );
}

function StartPanel({
  onRun,
}: {
  onRun: (repoUrl: string, userNotes: string, threadName?: string) => Promise<void>;
}) {
  const [repoUrl, setRepoUrl] = React.useState("");
  const [threadName, setThreadName] = React.useState("");
  const [userNotes, setUserNotes] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  return (
    <div className="rounded-xl border bg-white/70 p-5 shadow-sm backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/40">
      <div className="text-sm font-medium">StartPanel</div>
      <div className="mt-3 grid gap-3">
        <input
          className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-950"
          placeholder="GitHub URL or local path (e.g. https://github.com/owner/repo or D:\\projects\\my-tool)"
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
        />
        <div className="text-[11px] text-zinc-500">
          支持远程仓库和本地目录。输入本地目录时，可一键在该目录生成 <code>.aui-dashboard</code>。
        </div>
        <input
          className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-950"
          placeholder="Task name (optional, e.g. user-dashboard)"
          value={threadName}
          onChange={(e) => setThreadName(e.target.value)}
        />
        <textarea
          className="min-h-24 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-950"
          placeholder="User notes / goals..."
          value={userNotes}
          onChange={(e) => setUserNotes(e.target.value)}
        />
        <button
          type="button"
          disabled={submitting || !repoUrl.trim()}
          className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
          onClick={async () => {
            setSubmitting(true);
            try {
              await onRun(repoUrl, userNotes, threadName || undefined);
            } finally {
              setSubmitting(false);
            }
          }}
        >
          {submitting ? "Running..." : "Run"}
        </button>
      </div>
    </div>
  );
}

function RollbackModal({
  anchor,
  onClose,
  onConfirm,
}: {
  anchor: HistoryItem;
  onClose: () => void;
  onConfirm: (userNotes: string, imageUrl?: string) => Promise<void>;
}) {
  const [notes, setNotes] = React.useState("");
  const [imageUrl, setImageUrl] = React.useState<string>("");
  const [loading, setLoading] = React.useState(false);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl border bg-white p-5 shadow-xl dark:border-zinc-800 dark:bg-zinc-950">
        <div className="text-base font-semibold">回溯（可选 Prompt）</div>
        <div className="mt-2 text-sm text-zinc-600 dark:text-zinc-300">
          锚点：<span className="font-medium">{anchor.last_step_id ?? "unknown"}</span>
        </div>
        <div className="mt-2 rounded-lg border bg-zinc-50 p-3 text-xs text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/50 dark:text-zinc-200">
          {anchor.checkpoint_summary ?? "(no checkpoint_summary)"}
        </div>
        <textarea
          className="mt-3 min-h-24 w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-950"
          placeholder="可选：输入新的 prompt（不填则直接回溯执行）"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        <div className="mt-3">
          <label className="mb-1 block text-xs text-zinc-500">可选图片（Vision 占位）</label>
          <input
            type="file"
            accept="image/*"
            className="block w-full text-xs"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) {
                setImageUrl("");
                return;
              }
              const reader = new FileReader();
              reader.onload = () => {
                const result = reader.result;
                if (typeof result === "string") setImageUrl(result);
              };
              reader.readAsDataURL(file);
            }}
          />
          {imageUrl ? <div className="mt-1 text-[11px] text-zinc-500">image attached</div> : null}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="rounded-lg border px-3 py-2 text-sm" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            disabled={loading}
            className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
            onClick={async () => {
              setLoading(true);
              try {
                await onConfirm(notes, imageUrl || undefined);
              } finally {
                setLoading(false);
              }
            }}
          >
            {loading ? "提交中..." : notes.trim() ? "回溯并应用 Prompt" : "直接回溯"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Home() {
  const [threadId, setThreadId] = React.useState<string | null>(null);
  const [stateResp, setStateResp] = React.useState<ThreadStateResponse | null>(null);
  const [isRunning, setIsRunning] = React.useState(false);
  const [toast, setToast] = React.useState<string | null>(null);
  const [anchorForRollback, setAnchorForRollback] = React.useState<HistoryItem | null>(null);
  const [modifyModalOpen, setModifyModalOpen] = React.useState(false);
  const [contextUiPayload, setContextUiPayload] = React.useState<ContextUiPayload | null>(null);
  const [modifyingComponentKey, setModifyingComponentKey] = React.useState<string | null>(null);
  const [targetProjectRoot, setTargetProjectRoot] = React.useState("");
  const [exportTargetDir, setExportTargetDir] = React.useState("");
  const [exportMode, setExportMode] = React.useState<"copy" | "sync">("copy");
  const [isExporting, setIsExporting] = React.useState(false);
  const [bridgePreview, setBridgePreview] = React.useState<BridgePreview | null>(null);
  const [bridgeTargetProjectRoot, setBridgeTargetProjectRoot] = React.useState("");
  const [isBridgeLoading, setIsBridgeLoading] = React.useState(false);
  const [isBridgeGenerating, setIsBridgeGenerating] = React.useState(false);
  const [isBridgeRescanning, setIsBridgeRescanning] = React.useState(false);
  const [bridgeLaunchCommand, setBridgeLaunchCommand] = React.useState("");
  const [sidecarStatus, setSidecarStatus] = React.useState<SidecarStatusResponse | null>(null);
  const [isSidecarChecking, setIsSidecarChecking] = React.useState(false);
  const [syncLogicOnly, setSyncLogicOnly] = React.useState(false);
  const [dashboardBgUrl, setDashboardBgUrl] = React.useState(DEFAULT_DASHBOARD_BG_URL);
  const [dashboardBgOpacity, setDashboardBgOpacity] = React.useState(0.78);
  const renderPanelRef = React.useRef<HTMLDivElement | null>(null);
  const completionNoticeKeyRef = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const savedUrl = window.localStorage.getItem(DASHBOARD_BG_URL_STORAGE_KEY);
      if (savedUrl !== null) {
        setDashboardBgUrl(savedUrl);
      }
      const savedOpacity = window.localStorage.getItem(DASHBOARD_BG_OPACITY_STORAGE_KEY);
      if (savedOpacity !== null) {
        const parsed = Number(savedOpacity);
        if (Number.isFinite(parsed)) {
          setDashboardBgOpacity(Math.max(0, Math.min(1, parsed)));
        }
      }
    } catch {
      // no-op for restricted browser environments
    }
  }, []);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(DASHBOARD_BG_URL_STORAGE_KEY, dashboardBgUrl);
      window.localStorage.setItem(DASHBOARD_BG_OPACITY_STORAGE_KEY, String(dashboardBgOpacity));
    } catch {
      // no-op for restricted browser environments
    }
  }, [dashboardBgOpacity, dashboardBgUrl]);

  const showToast = React.useCallback((message: string, timeoutMs = 4500) => {
    setToast(message);
    setTimeout(() => setToast(null), timeoutMs);
  }, []);

  const showRateLimitToast = React.useCallback((detail: string) => {
    const lower = detail.toLowerCase();
    if (detail.includes("GitHub API") || detail.includes("限流") || lower.includes("rate limit")) {
      showToast("检测到 GitHub API 限流，请在设置中配置 GITHUB_TOKEN。");
    }
  }, [showToast]);

  const scrollToRenderPanel = React.useCallback(() => {
    renderPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const refreshState = React.useCallback(async (tid: string) => {
    const latest = await fetchThreadState(tid);
    setStateResp(latest);
  }, []);

  const refreshBridgePreview = React.useCallback(
    async (tid: string, silent = false) => {
      setIsBridgeLoading(true);
      try {
        const res = await fetchThreadBridgePreview(tid);
        setBridgePreview(res.preview);
        if (!silent) showToast("Bridge 预览已更新。");
      } catch (e) {
        if (!silent) {
          if (e instanceof ApiError) showToast(`Bridge 预览失败: ${e.detail}`);
          else showToast("Bridge 预览失败: 未知错误");
        }
      } finally {
        setIsBridgeLoading(false);
      }
    },
    [showToast]
  );

  const refreshSidecarStatus = React.useCallback(
    async (tid: string, targetRoot?: string, silent = true) => {
      setIsSidecarChecking(true);
      try {
        const res = await fetchThreadSidecarStatus(tid, targetRoot);
        setSidecarStatus(res);
        if (!res.sidecar_exists || !res.protocol_exists) {
          setSyncLogicOnly(false);
        }
        if (!silent) {
          const msg = res.sidecar_exists
            ? `.aui-dashboard detected at ${res.sidecar_root}`
            : `No .aui-dashboard at ${res.target_project_root}`;
          showToast(msg, 4000);
        }
      } catch (e) {
        setSidecarStatus(null);
        setSyncLogicOnly(false);
        if (!silent) {
          if (e instanceof ApiError) showToast(`检测 sidecar 失败: ${e.detail}`);
          else showToast("检测 sidecar 失败: 未知错误");
        }
      } finally {
        setIsSidecarChecking(false);
      }
    },
    [showToast]
  );

  React.useEffect(() => {
    const cmd = bridgePreview?.launch_profile?.recommended_command || "";
    if (!cmd) return;
    setBridgeLaunchCommand((prev) => (prev.trim() ? prev : cmd));
  }, [bridgePreview?.launch_profile?.recommended_command]);

  const handleRun = React.useCallback(
    async (repoUrl: string, userNotes: string, threadName?: string) => {
      setIsRunning(true);
      try {
        const res = await runThread({
          repo_url: repoUrl,
          user_notes: userNotes,
          thread_name: threadName,
          max_steps: 1,
        });
        setThreadId(res.thread_id);
        await refreshState(res.thread_id);
        await refreshBridgePreview(res.thread_id, true);
      } catch (e) {
        if (e instanceof ApiError) {
          showRateLimitToast(e.detail);
          showToast(`运行失败: ${e.detail}`);
        } else {
          showToast("运行失败: 无法连接后端，请确认 http://127.0.0.1:8000 已启动。");
        }
      } finally {
        setIsRunning(false);
      }
    },
    [refreshBridgePreview, refreshState, showRateLimitToast, showToast]
  );

  const handleRunNextStep = React.useCallback(async () => {
    if (!threadId) return;
    const repoUrl = stateResp?.values?.repo_url;
    if (!repoUrl) return;

    setIsRunning(true);
    try {
      await runThread({
        thread_id: threadId,
        repo_url: repoUrl,
        user_notes: "",
        max_steps: 1,
      });
      await refreshState(threadId);
      await refreshBridgePreview(threadId, true);
    } catch (e) {
      if (e instanceof ApiError) {
        showRateLimitToast(e.detail);
        showToast(`执行下一步失败: ${e.detail}`);
      } else {
        showToast("执行下一步失败: 无法连接后端，请确认 http://127.0.0.1:8000 已启动。");
      }
    } finally {
      setIsRunning(false);
    }
  }, [refreshBridgePreview, refreshState, showRateLimitToast, showToast, stateResp?.values?.repo_url, threadId]);

  const handleExportUI = React.useCallback(async () => {
    if (!threadId) return;
    const projectRoot = targetProjectRoot.trim();
    const target = exportTargetDir.trim();
    if (!projectRoot && !target) {
      showToast("请先填写目标项目根目录，或填写高级导出目录。");
      return;
    }

    setIsExporting(true);
    try {
      const payload = projectRoot
        ? {
            target_project_root: projectRoot,
            mode: exportMode,
          }
        : {
            target_dir: target,
            mode: exportMode,
          };
      const res = await exportThreadUI(threadId, payload);
      if (res.integration) {
        showToast(
          `UI已${exportMode === "sync" ? "同步" : "导出"}到项目: ${res.integration.integration_ui_dir}（入口: ${res.integration.entry_file_path}）`,
          8000
        );
      } else {
        showToast(`UI 已${exportMode === "sync" ? "同步" : "导出"}到: ${res.target_dir}`);
      }
    } catch (e) {
      if (e instanceof ApiError) {
        showRateLimitToast(e.detail);
        showToast(`导出失败: ${e.detail}`);
      } else {
        showToast("导出失败: 未知错误");
      }
    } finally {
      setIsExporting(false);
    }
  }, [exportMode, exportTargetDir, showRateLimitToast, showToast, targetProjectRoot, threadId]);

  const repoSource = (stateResp?.values?.repo_url || "").trim();
  const resolvedLocalProjectRoot = normalizeLocalRepoPath(repoSource);
  const isLocalSource = Boolean(resolvedLocalProjectRoot);

  React.useEffect(() => {
    if (!threadId) return;
    const targetRoot = bridgeTargetProjectRoot.trim() || targetProjectRoot.trim() || resolvedLocalProjectRoot.trim();
    if (!targetRoot && !isLocalSource) {
      setSidecarStatus(null);
      setSyncLogicOnly(false);
      return;
    }
    const timer = setTimeout(() => {
      void refreshSidecarStatus(threadId, targetRoot || undefined, true);
    }, 250);
    return () => clearTimeout(timer);
  }, [
    bridgeTargetProjectRoot,
    isLocalSource,
    refreshSidecarStatus,
    resolvedLocalProjectRoot,
    targetProjectRoot,
    threadId,
  ]);

  const handleGenerateBridge = React.useCallback(async () => {
    if (!threadId) return;
    const targetRoot = bridgeTargetProjectRoot.trim() || targetProjectRoot.trim() || resolvedLocalProjectRoot.trim();
    const launchOverride = bridgeLaunchCommand.trim();
    if (!targetRoot && !isLocalSource) {
      showToast("请先填写 Bridge 目标项目根目录（远程 GitHub 项目必填）。");
      return;
    }
    if (syncLogicOnly && (!sidecarStatus?.sidecar_exists || !sidecarStatus?.protocol_exists)) {
      showToast("仅同步逻辑要求目标目录已存在 .aui-dashboard 且含 aui_ui_protocol.json。");
      return;
    }
    setIsBridgeGenerating(true);
    try {
      const payload =
        targetRoot || !isLocalSource
          ? {
              target_project_root: targetRoot || undefined,
              override_launch_command: launchOverride || undefined,
              sync_logic_only: syncLogicOnly,
            }
          : { override_launch_command: launchOverride || undefined, sync_logic_only: syncLogicOnly };
      const res = await generateThreadBridge(threadId, payload);
      if (res.operation_mode === "sync_logic_only") {
        showToast(`逻辑已同步（协议保留）: ${res.target_bridge_dir}`, 8000);
      } else {
        showToast(`Bridge 已生成: ${res.target_bridge_dir}`, 8000);
      }
      await refreshBridgePreview(threadId, true);
      await refreshSidecarStatus(threadId, targetRoot || undefined, true);
    } catch (e) {
      if (e instanceof ApiError) showToast(`Bridge 生成失败: ${e.detail}`);
      else showToast("Bridge 生成失败: 未知错误");
    } finally {
      setIsBridgeGenerating(false);
    }
  }, [
    bridgeLaunchCommand,
    bridgeTargetProjectRoot,
    isLocalSource,
    refreshBridgePreview,
    refreshSidecarStatus,
    resolvedLocalProjectRoot,
    showToast,
    sidecarStatus?.protocol_exists,
    sidecarStatus?.sidecar_exists,
    syncLogicOnly,
    targetProjectRoot,
    threadId,
  ]);

  const handleRescanBridge = React.useCallback(async () => {
    if (!threadId) return;
    const targetRoot = bridgeTargetProjectRoot.trim() || targetProjectRoot.trim() || resolvedLocalProjectRoot.trim();
    const launchOverride = bridgeLaunchCommand.trim();
    if (!targetRoot && !isLocalSource) {
      showToast("远程项目请先填写目标项目根目录后再重新扫描。");
      return;
    }
    setIsBridgeRescanning(true);
    try {
      const payload = {
        target_project_root: targetRoot || undefined,
        override_launch_command: launchOverride || undefined,
      };
      const res = await rescanThreadBridge(threadId, payload);
      setBridgePreview(res.preview);
      if (res.preview?.launch_profile?.recommended_command) {
        setBridgeLaunchCommand(res.preview.launch_profile.recommended_command);
      }
      showToast(`已重新扫描并更新协议: ${res.target_bridge_dir}`, 7000);
      await refreshSidecarStatus(threadId, targetRoot || undefined, true);
    } catch (e) {
      if (e instanceof ApiError) showToast(`重新扫描失败: ${e.detail}`);
      else showToast("重新扫描失败: 未知错误");
    } finally {
      setIsBridgeRescanning(false);
    }
  }, [
    bridgeLaunchCommand,
    bridgeTargetProjectRoot,
    isLocalSource,
    refreshSidecarStatus,
    resolvedLocalProjectRoot,
    showToast,
    targetProjectRoot,
    threadId,
  ]);

  React.useEffect(() => {
    if (!threadId) return;
    const timer = setInterval(async () => {
      try {
        await refreshState(threadId);
      } catch (e) {
        if (e instanceof ApiError) showRateLimitToast(e.detail);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [threadId, refreshState, showRateLimitToast]);

  React.useEffect(() => {
    if (!threadId) return;
    if (exportTargetDir.trim()) return;
    setExportTargetDir(`./exports/${threadId}`);
  }, [threadId, exportTargetDir]);

  React.useEffect(() => {
    if (!threadId) return;
    if (targetProjectRoot.trim()) return;
    const repoUrl = stateResp?.values?.repo_url?.trim();
    if (!repoUrl) return;
    const localPath = normalizeLocalRepoPath(repoUrl);
    if (localPath) setTargetProjectRoot(localPath);
  }, [threadId, stateResp?.values?.repo_url, targetProjectRoot]);

  React.useEffect(() => {
    if (!threadId) return;
    if (bridgeTargetProjectRoot.trim()) return;
    if (targetProjectRoot.trim()) {
      setBridgeTargetProjectRoot(targetProjectRoot.trim());
      return;
    }
    const repoUrl = stateResp?.values?.repo_url?.trim();
    if (!repoUrl) return;
    const localPath = normalizeLocalRepoPath(repoUrl);
    if (localPath) setBridgeTargetProjectRoot(localPath);
  }, [bridgeTargetProjectRoot, stateResp?.values?.repo_url, targetProjectRoot, threadId]);

  const lastStepId = stateResp?.values?.last_step_id ?? null;
  const currentStepIndex = stateResp?.values?.current_step_index ?? 0;
  const totalSteps = stateResp?.values?.plan?.length ?? 0;
  const isCompleted = Boolean(totalSteps > 0 && typeof currentStepIndex === "number" && currentStepIndex >= totalSteps);
  const canRunNextStep = Boolean(
    threadId &&
      stateResp?.values?.repo_url &&
      totalSteps > 0 &&
      typeof currentStepIndex === "number" &&
      currentStepIndex < totalSteps
  );
  const stepOutput = lastStepId ? stateResp?.values?.step_outputs?.[lastStepId] : undefined;
  const rawUiRender = stepOutput?.ui_render;
  const isThinFallbackCard =
    rawUiRender?.type === "info_card" &&
    rawUiRender.content &&
    typeof rawUiRender.content === "object" &&
    !Array.isArray(rawUiRender.content) &&
    typeof (rawUiRender.content as Record<string, unknown>).text === "string" &&
    (((rawUiRender.content as Record<string, string>).text || "").includes("未提供 ui_render 细节") ||
      ((rawUiRender.content as Record<string, string>).text || "").includes("No detailed ui_render provided"));
  const uiRender = isThinFallbackCard
    ? {
        type: "info_card",
        content: {
          title: "执行摘要（自动兜底）",
          text: stepOutput?.checkpoint_summary || `${lastStepId ?? "step"} 已完成`,
          items: [
            `当前步骤: ${lastStepId ?? "unknown"}`,
            "默认保留状态栏与锚点回溯机制。",
            "如需改变结果，可在锚点回溯时填写 prompt（可选）。",
          ],
        },
      }
    : rawUiRender;
  const contextualComponentKey =
    stateResp?.values?.context_ui &&
    typeof stateResp.values.context_ui === "object" &&
    typeof (stateResp.values.context_ui as Record<string, unknown>).target_id === "string"
      ? ((stateResp.values.context_ui as Record<string, unknown>).target_id as string)
      : null;

  React.useEffect(() => {
    if (!threadId || !isCompleted || totalSteps <= 0) return;
    const key = `${threadId}:${lastStepId ?? "done"}:${totalSteps}`;
    if (completionNoticeKeyRef.current === key) return;
    completionNoticeKeyRef.current = key;
    showToast("流程已完成，已展示最新 UI。你可以直接导出/同步到目标项目。", 7000);
    setTimeout(() => scrollToRenderPanel(), 80);
  }, [isCompleted, lastStepId, scrollToRenderPanel, showToast, threadId, totalSteps]);

  const trimmedBgUrl = dashboardBgUrl.trim();
  const hasDashboardBg = Boolean(trimmedBgUrl);
  const clampedBgOpacity = Math.max(0, Math.min(1, dashboardBgOpacity));
  const dashboardShellStyle: React.CSSProperties | undefined = hasDashboardBg
    ? {
        backgroundColor: "#f4f6f8",
        backgroundImage: `linear-gradient(to bottom, rgba(244,246,248,${clampedBgOpacity}), rgba(244,246,248,${Math.min(
          1,
          clampedBgOpacity + 0.1
        )})), url("${trimmedBgUrl}")`,
        backgroundSize: "cover",
        backgroundPosition: "center",
        backgroundRepeat: "no-repeat",
        backgroundAttachment: "fixed",
      }
    : undefined;

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900 dark:bg-black dark:text-zinc-50" style={dashboardShellStyle}>
      {toast ? <Toast message={toast} /> : null}

      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="mb-6">
          <div className="text-xs font-medium text-zinc-500">AUI-Dashboard</div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">智能控制台</h1>
          <div className="mt-3 rounded-xl border bg-white/75 p-3 text-xs shadow-sm backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/50">
            <div className="font-medium text-zinc-600 dark:text-zinc-200">Dashboard Background</div>
            <input
              className="mt-2 w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs outline-none focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-950"
              value={dashboardBgUrl}
              onChange={(e) => setDashboardBgUrl(e.target.value)}
              placeholder="背景图 URL（例如 /bg.jpg 或 https://...）"
            />
            <div className="mt-2 flex items-center gap-2">
              <span className="shrink-0 text-[11px] text-zinc-500">遮罩透明度</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                className="w-full"
                value={clampedBgOpacity}
                onChange={(e) => setDashboardBgOpacity(Number(e.target.value || 0))}
              />
              <span className="w-12 text-right text-[11px] text-zinc-500">{Math.round(clampedBgOpacity * 100)}%</span>
              <button
                type="button"
                className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-[11px] text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200 dark:hover:bg-zinc-900"
                onClick={() => setDashboardBgUrl("")}
              >
                清空
              </button>
            </div>
          </div>
        </div>

        {!threadId ? (
          <StartPanel onRun={handleRun} />
        ) : (
          <>
            <ChronosBar
              threadId={threadId}
              isRunning={isRunning}
              onSelectAnchor={(anchor) => {
                setAnchorForRollback(anchor);
              }}
            />

            {isCompleted ? (
              <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-900 shadow-sm dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-200">
                <div className="text-sm font-semibold">流程已完成</div>
                <div className="mt-1 text-xs">最新 UI 已生成并显示在渲染区，你可以直接导出或同步到目标项目。</div>
                <button
                  type="button"
                  className="mt-3 rounded-lg border border-emerald-300 bg-white px-3 py-2 text-xs font-medium text-emerald-700 hover:bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-200 dark:hover:bg-emerald-900/30"
                  onClick={scrollToRenderPanel}
                >
                  查看最新 UI
                </button>
              </div>
            ) : null}

            <div className="mt-6 grid gap-6 md:grid-cols-2">
              <div ref={renderPanelRef} className="rounded-xl border bg-white/70 p-4 shadow-sm backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/40">
                <div className="text-sm font-medium">协议化 UI 渲染区</div>
                <div className="mt-2 text-xs text-zinc-500">
                  source: step_outputs[{lastStepId ?? "last_step_id"}].ui_render
                </div>
                <div className="mt-4">
                  <UIInterpreter
                    ui={uiRender}
                    modifyingComponentKey={modifyingComponentKey}
                    contextualComponentKey={contextualComponentKey}
                    onModify={(payload) => {
                      setContextUiPayload(payload);
                      setModifyModalOpen(true);
                    }}
                  />
                </div>
              </div>

              <div className="rounded-xl border bg-white/70 p-4 shadow-sm backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/40">
                <div className="text-sm font-medium">Thread 状态</div>
                <pre className="mt-3 overflow-auto rounded-lg bg-zinc-50 p-3 text-xs dark:bg-zinc-900/50">
                  {JSON.stringify(
                    {
                      threadId,
                      checkpointId: stateResp?.checkpoint_id,
                      currentStepIndex: stateResp?.values?.current_step_index,
                      lastStepId,
                    },
                    null,
                    2
                  )}
                </pre>
                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    disabled={isRunning || !canRunNextStep}
                    className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                    onClick={handleRunNextStep}
                  >
                    {isRunning ? "Running..." : "Run Next Step"}
                  </button>
                  <span className="text-xs text-zinc-500">
                    {totalSteps > 0 ? `${Math.min(currentStepIndex, totalSteps)}/${totalSteps}` : "No plan yet"}
                  </span>
                </div>

                <div className="mt-4 rounded-lg border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900/40">
                  <div className="mb-2 text-xs font-medium text-zinc-600 dark:text-zinc-300">Export / Sync UI</div>
                  <input
                    className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs outline-none focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-950"
                    value={targetProjectRoot}
                    onChange={(e) => setTargetProjectRoot(e.target.value)}
                    placeholder="Target project root (recommended, e.g. D:\\my-project)"
                  />
                  <div className="mt-1 text-[11px] text-zinc-500">
                    将自动写入: {"<project-root>/.aui-dashboard/ui/<thread_id>"}，并生成 {"AUI_UI_ENTRY.json"} 对接文件。
                  </div>
                  <div className="mt-1 text-[11px] text-zinc-500">
                    说明：<code>AUI_UI_ENTRY.json</code> 是静态导出入口；项目运行控制请使用
                    <code> python ./.aui-dashboard/ui/ui_runner.py</code>。
                  </div>
                  <input
                    className="mt-2 w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs outline-none focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-950"
                    value={exportTargetDir}
                    onChange={(e) => setExportTargetDir(e.target.value)}
                    placeholder="Advanced: custom export directory (optional)"
                  />
                  <div className="mt-2 flex items-center gap-2">
                    <select
                      className="rounded-lg border border-zinc-200 bg-white px-2 py-2 text-xs dark:border-zinc-800 dark:bg-zinc-950"
                      value={exportMode}
                      onChange={(e) => setExportMode(e.target.value as "copy" | "sync")}
                    >
                      <option value="copy">Copy</option>
                      <option value="sync">Sync</option>
                    </select>
                    <button
                      type="button"
                      disabled={isExporting || !threadId}
                      className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                      onClick={handleExportUI}
                    >
                      {isExporting ? "Exporting..." : exportMode === "sync" ? "Sync UI" : "Export UI"}
                    </button>
                  </div>
                </div>

                <div className="mt-4 rounded-lg border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900/40">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <div className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
                      Bridge Preview (Non-invasive)
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        disabled={!threadId || isBridgeLoading}
                        className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-[11px] text-zinc-700 hover:bg-zinc-50 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200 dark:hover:bg-zinc-900"
                        onClick={async () => {
                          if (!threadId) return;
                          await refreshBridgePreview(threadId);
                        }}
                      >
                        {isBridgeLoading ? "Loading..." : "Refresh Preview"}
                      </button>
                      <button
                        type="button"
                        disabled={!threadId || isBridgeRescanning}
                        className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-[11px] text-zinc-700 hover:bg-zinc-50 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200 dark:hover:bg-zinc-900"
                        onClick={handleRescanBridge}
                      >
                        {isBridgeRescanning ? "Rescanning..." : "重新扫描并更新协议"}
                      </button>
                    </div>
                  </div>

                  {bridgePreview ? (
                    <div className="space-y-2 text-[11px] text-zinc-600 dark:text-zinc-300">
                      <div>
                        <span className="font-medium text-zinc-700 dark:text-zinc-200">Core:</span>{" "}
                        {bridgePreview.core_functionality}
                      </div>
                      <div>
                        <span className="font-medium text-zinc-700 dark:text-zinc-200">Source:</span>{" "}
                        {bridgePreview.analysis_source.source_type || "unknown"} /{" "}
                        {bridgePreview.analysis_source.readme_file || "README.md"}
                      </div>
                      <div>
                        <span className="font-medium text-zinc-700 dark:text-zinc-200">Startup Files:</span>{" "}
                        {(bridgePreview.analysis_source.startup_files || []).join(", ") || "(none)"}
                      </div>
                      <div>
                        <span className="font-medium text-zinc-700 dark:text-zinc-200">Launch:</span>{" "}
                        <code>{bridgePreview.launch_profile.recommended_command}</code>
                      </div>
                      <div>
                        <span className="font-medium text-zinc-700 dark:text-zinc-200">Stages:</span>{" "}
                        {bridgePreview.ui_protocol.status_bar.stages.map((s) => s.label).join(" -> ")}
                      </div>
                      <div>
                        <span className="font-medium text-zinc-700 dark:text-zinc-200">Controls:</span>{" "}
                        {bridgePreview.ui_protocol.controls.length}
                      </div>
                    </div>
                  ) : (
                    <div className="text-[11px] text-zinc-500">暂无预览。点击 Refresh Preview 生成。</div>
                  )}
                  <textarea
                    className="mt-3 min-h-20 w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs outline-none focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-950"
                    value={bridgeLaunchCommand}
                    onChange={(e) => setBridgeLaunchCommand(e.target.value)}
                    placeholder="启动命令（可编辑），例如: python core.py --port 8000"
                  />
                  <div className="mt-1 text-[11px] text-zinc-500">
                    这个命令会写入 <code>aui_bridge_config.json</code>，如识别有误可直接改这里。
                  </div>

                  <input
                    className="mt-3 w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs outline-none focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-950"
                    value={bridgeTargetProjectRoot}
                    onChange={(e) => setBridgeTargetProjectRoot(e.target.value)}
                    placeholder="Bridge target project root (e.g. D:\\my-project)"
                  />
                  <div className="mt-1 text-[11px] text-zinc-500">
                    将写入: {"<project-root>/.aui-dashboard/bridge"}（不会修改项目原文件）
                  </div>
                  {isLocalSource ? (
                    <div className="mt-1 text-[11px] text-emerald-700 dark:text-emerald-300">
                      本地项目模式：可直接一键生成到 {resolvedLocalProjectRoot || "(本地目录)"}。
                    </div>
                  ) : null}
                  <div className="mt-1 text-[11px] text-zinc-500">
                    {isSidecarChecking
                      ? "检测目标 sidecar 中..."
                      : sidecarStatus
                      ? sidecarStatus.sidecar_exists
                        ? `检测到 sidecar: ${sidecarStatus.sidecar_root}`
                        : "未检测到 .aui-dashboard（将执行完整生成）"
                      : "输入目标目录后会自动检测 .aui-dashboard"}
                  </div>
                  <label className="mt-2 flex items-center gap-2 text-[11px] text-zinc-600 dark:text-zinc-300">
                    <input
                      type="checkbox"
                      checked={syncLogicOnly}
                      onChange={(e) => setSyncLogicOnly(e.target.checked)}
                      disabled={Boolean(
                        isBridgeGenerating ||
                          !sidecarStatus?.sidecar_exists ||
                          !sidecarStatus?.protocol_exists
                      )}
                    />
                    仅同步逻辑 (Sync Logic Only，保留现有 aui_ui_protocol.json)
                  </label>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={!threadId || isBridgeGenerating}
                      className="rounded-lg bg-amber-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
                      onClick={handleGenerateBridge}
                    >
                      {isBridgeGenerating
                        ? "Generating Bridge..."
                        : syncLogicOnly
                        ? "Sync Logic Only"
                        : isLocalSource
                        ? "一键生成到本地项目"
                        : "Generate aui_bridge.py"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      {anchorForRollback && threadId ? (
        <RollbackModal
          anchor={anchorForRollback}
          onClose={() => setAnchorForRollback(null)}
          onConfirm={async (newNotes, imageUrl) => {
            setIsRunning(true);
            try {
              await rollbackThread({
                thread_id: threadId,
                checkpoint_id: anchorForRollback.checkpoint_id,
                user_notes: newNotes,
                image_url: imageUrl || null,
              });
              await refreshState(threadId);
              await refreshBridgePreview(threadId, true);
              setAnchorForRollback(null);
            } catch (e) {
              if (e instanceof ApiError) showRateLimitToast(e.detail);
              throw e;
            } finally {
              setIsRunning(false);
            }
          }}
        />
      ) : null}

      {modifyModalOpen && threadId && lastStepId ? (
        <RollbackModal
          anchor={{
            checkpoint_id: stateResp?.checkpoint_id ?? null,
            current_step_index: stateResp?.values?.current_step_index ?? null,
            last_step_id: lastStepId,
            checkpoint_summary:
              stateResp?.values?.step_outputs?.[lastStepId]?.checkpoint_summary ?? "Component-level modify",
            replan_required: false,
          }}
          onClose={() => setModifyModalOpen(false)}
          onConfirm={async (newNotes, imageUrl) => {
            setIsRunning(true);
            setModifyingComponentKey(contextUiPayload?.componentKey ?? "unknown");
            try {
              await rollbackThread({
                thread_id: threadId,
                step_id: lastStepId,
                user_notes: newNotes,
                context_ui: contextUiPayload
                  ? ({
                      target_id: contextUiPayload.componentKey,
                      component_type: contextUiPayload.componentType,
                      original_spec:
                        contextUiPayload.componentJson &&
                        typeof contextUiPayload.componentJson === "object" &&
                        !Array.isArray(contextUiPayload.componentJson)
                          ? (contextUiPayload.componentJson as Record<string, unknown>)
                          : {},
                      user_intent: newNotes,
                      scope: contextUiPayload.scope,
                    } satisfies UIModificationContext)
                  : null,
                image_url: imageUrl || null,
              });
              await refreshState(threadId);
              await refreshBridgePreview(threadId, true);
              setModifyModalOpen(false);
              setContextUiPayload(null);
            } catch (e) {
              if (e instanceof ApiError) showRateLimitToast(e.detail);
              throw e;
            } finally {
              setIsRunning(false);
              setModifyingComponentKey(null);
            }
          }}
        />
      ) : null}
    </div>
  );
}
