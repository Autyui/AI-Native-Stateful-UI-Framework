"use client";

import * as React from "react";
import { Clock, CornerDownLeft, Loader2 } from "lucide-react";

import { fetchAnchorsOverview } from "@/lib/api";
import type { AnchorsOverviewResponse, HistoryItem } from "@/lib/types";

type ChronosBarProps = {
  threadId: string;
  onSelectAnchor?: (anchor: HistoryItem) => void;
  pollMs?: number;
  isRunning?: boolean;
};

function cn(...classes: Array<string | undefined | false>) {
  return classes.filter(Boolean).join(" ");
}

export function ChronosBar({ threadId, onSelectAnchor, pollMs = 1500, isRunning = false }: ChronosBarProps) {
  const [overview, setOverview] = React.useState<AnchorsOverviewResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [selected, setSelected] = React.useState<HistoryItem | null>(null);
  const [showAllHistory, setShowAllHistory] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      setError(null);
      const data = await fetchAnchorsOverview(threadId);
      setOverview(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [threadId]);

  React.useEffect(() => {
    setLoading(true);
    void load();
  }, [load]);

  React.useEffect(() => {
    const t = setInterval(() => void load(), pollMs);
    return () => clearInterval(t);
  }, [load, pollMs]);

  const allItems = React.useMemo(() => overview?.anchors ?? [], [overview?.anchors]);
  const dedupedItems = React.useMemo(() => {
    const seen = new Set<string>();
    const out: HistoryItem[] = [];
    for (let i = allItems.length - 1; i >= 0; i -= 1) {
      const it = allItems[i];
      const key = it.last_step_id ?? `anchor_${i}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.unshift(it);
    }
    return out;
  }, [allItems]);
  const items = showAllHistory ? allItems : dedupedItems;
  const current = allItems[allItems.length - 1] || null;
  const isCompleted = Boolean(overview && overview.total_steps > 0 && overview.completed_steps >= overview.total_steps);
  const currentStepLabel = isCompleted ? "All steps completed" : overview?.current_step?.label ?? "Waiting to start";
  const nextStepLabel = isCompleted ? "Rollback from anchors to continue" : overview?.next_step?.label ?? "Pending";
  const progressPercent = overview?.progress_percent ?? 0;

  return (
    <div className="w-full rounded-xl border bg-white/70 p-4 shadow-sm backdrop-blur dark:bg-zinc-950/40">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Clock className="h-4 w-4 text-zinc-600 dark:text-zinc-300" />
          <div className="text-sm font-medium">Chronos Bar</div>
          {loading ? <Loader2 className="h-4 w-4 animate-spin text-zinc-500" /> : null}
        </div>
        <div className="text-xs text-zinc-500">
          {overview ? `${overview.completed_steps}/${overview.total_steps} steps` : "loading"}
        </div>
      </div>

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-200">
          {error}
        </div>
      ) : null}
      {overview?.state_source === "timeline" ? (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
          当前为 timeline 恢复模式（后端重启后从磁盘恢复）。状态栏可读，但历史 checkpoint 回溯暂不可用，请继续运行生成新 checkpoint。
        </div>
      ) : null}

      <div className="mb-3 rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-xs text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-200">
        <div className="mb-2 flex items-center justify-between">
          <div className={cn("font-medium", isCompleted ? "text-emerald-700 dark:text-emerald-300" : "")}>
            {isCompleted ? "Status: Completed" : "Status: Running"}
          </div>
          <button
            type="button"
            className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-[11px] text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200 dark:hover:bg-zinc-900"
            onClick={() => setShowAllHistory((x) => !x)}
          >
            {showAllHistory ? "Show Latest Per Step" : "Show All History"}
          </button>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          <div>
            <span className="font-medium">Current:</span> {currentStepLabel}
          </div>
          <div>
            <span className="font-medium">Next:</span> {nextStepLabel}
          </div>
        </div>
      </div>

      <div className="relative mt-3">
        <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
          <div className="h-full bg-blue-500 transition-all" style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }} />
        </div>
        <div className="mt-2 text-right text-[11px] text-zinc-500">{progressPercent.toFixed(2)}%</div>

        <div
          className={cn(
            "pointer-events-none absolute -top-0.5 h-3 w-24 rounded-full opacity-0 transition-opacity",
            loading ? "opacity-100" : "opacity-0"
          )}
          style={{
            background:
              "linear-gradient(90deg, rgba(59,130,246,0) 0%, rgba(59,130,246,0.65) 50%, rgba(59,130,246,0) 100%)",
            animation: "chronos-flow 1.2s linear infinite",
          }}
        />

        <div className="mt-3 flex flex-wrap items-center gap-2">
          {items.length === 0 ? (
            <div className="text-xs text-zinc-500">No checkpoints yet. Run the flow to create anchors.</div>
          ) : null}
          {items.map((it, idx) => {
            const isCurrent = current?.checkpoint_id === it.checkpoint_id;
            const canRollback = Boolean(it.checkpoint_id);
            return (
              <button
                key={it.checkpoint_id ?? idx}
                type="button"
                disabled={!canRollback}
                className={cn(
                  "group relative inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs transition",
                  "bg-white hover:bg-zinc-50 dark:bg-zinc-950 dark:hover:bg-zinc-900",
                  isCurrent
                    ? "border-blue-300 text-blue-700 dark:border-blue-800 dark:text-blue-200"
                    : "border-zinc-200 text-zinc-700 dark:border-zinc-800 dark:text-zinc-200",
                  isCurrent && isRunning ? "animate-pulse" : "",
                  !canRollback ? "cursor-not-allowed opacity-60" : ""
                )}
                title={
                  canRollback
                    ? it.checkpoint_summary ?? "no checkpoint_summary"
                    : "checkpoint 不可用（timeline 恢复模式）"
                }
                onClick={() => {
                  if (!canRollback) return;
                  setSelected(it);
                  onSelectAnchor?.(it);
                }}
              >
                <span className={cn("h-2 w-2 rounded-full", isCurrent ? "bg-blue-500" : "bg-zinc-400")} />
                <span className="font-medium">{it.last_step_id ?? "anchor"}</span>
                <span className="text-zinc-400">#{idx}</span>

                <span className="pointer-events-none absolute left-1/2 top-full z-10 mt-2 hidden w-80 -translate-x-1/2 rounded-lg border bg-white p-3 text-left text-xs text-zinc-700 shadow-lg group-hover:block dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-200">
                  <div className="mb-1 flex items-center gap-2 text-zinc-500">
                    <CornerDownLeft className="h-3 w-3" />
                    <span>checkpoint_summary</span>
                  </div>
                  <div className="whitespace-pre-wrap">{it.checkpoint_summary ?? "(empty)"}</div>
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {selected ? (
        <div className="mt-4 rounded-lg border bg-zinc-50 p-3 text-xs text-zinc-800 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-100">
          <div className="mb-1 font-medium">Selected anchor</div>
          <div className="text-zinc-600 dark:text-zinc-300">
            <div>
              <span className="font-medium">last_step_id:</span> {selected.last_step_id}
            </div>
            <div className="mt-1 whitespace-pre-wrap">
              <span className="font-medium">checkpoint_summary:</span> {selected.checkpoint_summary ?? "(empty)"}
            </div>
          </div>
        </div>
      ) : null}

      <style jsx>{`
        @keyframes chronos-flow {
          0% {
            transform: translateX(-10%);
          }
          100% {
            transform: translateX(110%);
          }
        }
      `}</style>
    </div>
  );
}
