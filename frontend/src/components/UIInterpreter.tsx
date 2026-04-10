"use client";

import * as React from "react";
import { Terminal } from "lucide-react";

import type { UIModificationScope, UIRender } from "@/lib/types";

function cn(...classes: Array<string | undefined | false>) {
  return classes.filter(Boolean).join(" ");
}

type ModifyPayload = {
  componentKey: string;
  componentType: string;
  componentJson: unknown;
  scope: UIModificationScope;
};

function ComponentWrapper({
  children,
  componentKey,
  componentType,
  componentJson,
  onModify,
  isModifying,
  isContextualTarget,
}: {
  children: React.ReactNode;
  componentKey: string;
  componentType: string;
  componentJson: unknown;
  onModify?: (payload: ModifyPayload) => void;
  isModifying?: boolean;
  isContextualTarget?: boolean;
}) {
  return (
    <div
      className={cn(
        "group relative rounded-xl",
        isModifying ? "animate-pulse" : "",
        isContextualTarget ? "ring-2 ring-violet-500/70" : ""
      )}
    >
      {onModify ? (
        <div className="absolute right-2 top-2 z-10 hidden items-center gap-1 group-hover:flex">
          <button
            type="button"
            className="rounded-md border bg-white/95 px-2 py-1 text-[11px] shadow-sm dark:border-zinc-700 dark:bg-zinc-950/95"
            onClick={() => onModify({ componentKey, componentType, componentJson, scope: "content" })}
            title="Modify content"
          >
            Modify
          </button>
          <button
            type="button"
            className="rounded-md border bg-white/95 px-2 py-1 text-[11px] shadow-sm dark:border-zinc-700 dark:bg-zinc-950/95"
            onClick={() => onModify({ componentKey, componentType, componentJson, scope: "style" })}
            title="Modify style"
          >
            Style
          </button>
        </div>
      ) : null}
      {children}
    </div>
  );
}

export function UIInterpreter({
  ui,
  onModify,
  modifyingComponentKey,
  contextualComponentKey,
}: {
  ui: UIRender | null | undefined;
  onModify?: (payload: ModifyPayload) => void;
  modifyingComponentKey?: string | null;
  contextualComponentKey?: string | null;
}) {
  const renderAutoFallback = (summaryText?: string) => (
    <div className="rounded-xl border bg-white p-5 text-sm shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
      <div className="mb-2 text-xs font-medium text-zinc-500">Auto fallback UI</div>
      <div className="text-sm font-semibold">Execution Summary (Fallback)</div>
      <div className="mt-2 text-sm text-zinc-700 dark:text-zinc-200">
        {summaryText?.trim() || "Current step finished, but no detailed ui_render payload was returned by the model."}
      </div>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-zinc-700 dark:text-zinc-200">
        <li>Status bar and anchor rollback remain available.</li>
        <li>You can rollback to an anchor and add a prompt refinement (optional).</li>
        <li>Or continue to run the next step.</li>
      </ul>
    </div>
  );

  if (!ui) {
    return renderAutoFallback();
  }

  const contentObj: Record<string, unknown> =
    ui.content && typeof ui.content === "object" && !Array.isArray(ui.content) ? (ui.content as Record<string, unknown>) : {};

  const getString = (k: string) => {
    const v = contentObj[k];
    return typeof v === "string" ? v : undefined;
  };

  const getStringArray = (k: string) => {
    const v = contentObj[k];
    return Array.isArray(v) && v.every((x) => typeof x === "string") ? (v as string[]) : undefined;
  };

  switch (ui.type) {
    case "info_card": {
      const title = getString("title");
      const text = getString("text");
      const items = getStringArray("items");
      const isThinFallbackText = Boolean(
        text &&
          (text.includes("No detailed ui_render provided") || text.includes("未提供 ui_render 细节"))
      );
      if (isThinFallbackText) {
        return renderAutoFallback(text);
      }
      return (
        <ComponentWrapper
          componentKey="info_card"
          componentType="info_card"
          componentJson={ui}
          onModify={onModify}
          isModifying={modifyingComponentKey === "info_card"}
          isContextualTarget={contextualComponentKey === "info_card"}
        >
          <div className="rounded-xl border bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
            {title ? <div className="mb-2 text-sm font-semibold">{title}</div> : null}
            {text ? <div className="text-sm text-zinc-700 dark:text-zinc-200">{text}</div> : null}
            {items ? (
              <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-zinc-700 dark:text-zinc-200">
                {items.map((x: string, i: number) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            ) : null}
          </div>
        </ComponentWrapper>
      );
    }
    case "terminal": {
      const title = getString("title") ?? "Terminal";
      const lines = getStringArray("lines") ?? [];
      return (
        <ComponentWrapper
          componentKey="terminal"
          componentType="terminal"
          componentJson={ui}
          onModify={onModify}
          isModifying={modifyingComponentKey === "terminal"}
          isContextualTarget={contextualComponentKey === "terminal"}
        >
          <div className="overflow-hidden rounded-xl border bg-black shadow-sm dark:border-zinc-800">
            <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2 text-xs text-zinc-300">
              <Terminal className="h-4 w-4" />
              <div className="font-medium">{title}</div>
            </div>
            <pre className="max-h-[360px] overflow-auto px-4 py-3 text-xs leading-5 text-green-200">
              {lines.join("\n")}
            </pre>
          </div>
        </ComponentWrapper>
      );
    }
    case "form": {
      const title = getString("title");
      const rawFields = contentObj["fields"];
      const fields =
        Array.isArray(rawFields) && rawFields.every((x) => x && typeof x === "object" && !Array.isArray(x))
          ? (rawFields as Array<Record<string, unknown>>)
          : [];
      return (
        <ComponentWrapper
          componentKey="form"
          componentType="form"
          componentJson={ui}
          onModify={onModify}
          isModifying={modifyingComponentKey === "form"}
          isContextualTarget={contextualComponentKey === "form"}
        >
          <div className="rounded-xl border bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
            {title ? <div className="mb-4 text-sm font-semibold">{title}</div> : null}
            <div className="grid gap-3">
              {fields.map((f, i) => (
                <label key={(typeof f.name === "string" && f.name) || i} className="grid gap-1 text-sm">
                  <span className="text-xs font-medium text-zinc-700 dark:text-zinc-200">
                    {(typeof f.label === "string" && f.label) ||
                      (typeof f.name === "string" && f.name) ||
                      `Field ${i + 1}`}
                  </span>
                  {f.kind === "textarea" ? (
                    <textarea
                      className={cn(
                        "min-h-20 rounded-lg border px-3 py-2 text-sm outline-none",
                        "border-zinc-200 bg-white text-zinc-900 focus:border-blue-400",
                        "dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100"
                      )}
                      placeholder={(typeof f.placeholder === "string" && f.placeholder) || ""}
                      readOnly
                    />
                  ) : (
                    <input
                      className={cn(
                        "rounded-lg border px-3 py-2 text-sm outline-none",
                        "border-zinc-200 bg-white text-zinc-900 focus:border-blue-400",
                        "dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100"
                      )}
                      placeholder={(typeof f.placeholder === "string" && f.placeholder) || ""}
                      readOnly
                    />
                  )}
                </label>
              ))}
            </div>
            <div className="mt-3 text-xs text-zinc-500">This is a render-only preview (MVP).</div>
          </div>
        </ComponentWrapper>
      );
    }
    default: {
      // Generic fallback renderer.
      return (
        <ComponentWrapper
          componentKey={ui.type}
          componentType={ui.type}
          componentJson={ui}
          onModify={onModify}
          isModifying={modifyingComponentKey === ui.type}
          isContextualTarget={contextualComponentKey === ui.type}
        >
          <div className="rounded-xl border bg-white p-5 text-sm shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
            <div className="mb-2 text-xs font-medium text-zinc-500">Unknown ui_render.type</div>
            <div className="text-sm font-semibold">{ui.type}</div>
            <pre className="mt-3 overflow-auto rounded-lg bg-zinc-50 p-3 text-xs text-zinc-800 dark:bg-zinc-900/50 dark:text-zinc-200">
              {JSON.stringify(ui, null, 2)}
            </pre>
          </div>
        </ComponentWrapper>
      );
    }
  }
}

