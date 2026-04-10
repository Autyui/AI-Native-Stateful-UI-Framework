import type {
  AnchorsOverviewResponse,
  BridgePreviewResponse,
  ExportUIRequest,
  ExportUIResponse,
  GenerateBridgeRequest,
  GenerateBridgeResponse,
  SidecarStatusResponse,
  RescanBridgeRequest,
  RescanBridgeResponse,
  HistoryItem,
  RollbackRequest,
  RunRequest,
  RunResponse,
  ThreadStateResponse,
} from "@/lib/types";

const DEFAULT_BASE_URL = "http://localhost:8000";

export function apiBaseUrl() {
  return process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_BASE_URL;
}

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function parseOrThrow(res: Response) {
  if (res.ok) return res;
  let detail = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    detail = body?.detail || detail;
  } catch {
    const text = await res.text();
    if (text) detail = text;
  }
  throw new ApiError(res.status, detail);
}

export async function fetchHistory(threadId: string, init?: RequestInit): Promise<HistoryItem[]> {
  const url = `${apiBaseUrl()}/history?thread_id=${encodeURIComponent(threadId)}`;
  const res = await fetch(url, { ...init, cache: "no-store" });
  await parseOrThrow(res);
  return (await res.json()) as HistoryItem[];
}

export async function fetchAnchorsOverview(threadId: string, init?: RequestInit): Promise<AnchorsOverviewResponse> {
  const url = `${apiBaseUrl()}/threads/${encodeURIComponent(threadId)}/anchors`;
  const res = await fetch(url, { ...init, cache: "no-store" });
  await parseOrThrow(res);
  return (await res.json()) as AnchorsOverviewResponse;
}

export async function runThread(payload: RunRequest): Promise<RunResponse> {
  const res = await fetch(`${apiBaseUrl()}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await parseOrThrow(res);
  return (await res.json()) as RunResponse;
}

export async function rollbackThread(payload: RollbackRequest) {
  const res = await fetch(`${apiBaseUrl()}/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await parseOrThrow(res);
  return res.json();
}

export async function fetchThreadState(threadId: string): Promise<ThreadStateResponse> {
  const res = await fetch(`${apiBaseUrl()}/threads/${encodeURIComponent(threadId)}/state`, {
    cache: "no-store",
  });
  await parseOrThrow(res);
  return (await res.json()) as ThreadStateResponse;
}

export async function exportThreadUI(threadId: string, payload: ExportUIRequest): Promise<ExportUIResponse> {
  const res = await fetch(`${apiBaseUrl()}/threads/${encodeURIComponent(threadId)}/export-ui`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await parseOrThrow(res);
  return (await res.json()) as ExportUIResponse;
}

export async function fetchThreadBridgePreview(threadId: string): Promise<BridgePreviewResponse> {
  const res = await fetch(`${apiBaseUrl()}/threads/${encodeURIComponent(threadId)}/bridge-preview`, {
    cache: "no-store",
  });
  await parseOrThrow(res);
  return (await res.json()) as BridgePreviewResponse;
}

export async function generateThreadBridge(
  threadId: string,
  payload: GenerateBridgeRequest
): Promise<GenerateBridgeResponse> {
  const res = await fetch(`${apiBaseUrl()}/threads/${encodeURIComponent(threadId)}/generate-bridge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await parseOrThrow(res);
  return (await res.json()) as GenerateBridgeResponse;
}

export async function fetchThreadSidecarStatus(
  threadId: string,
  targetProjectRoot?: string
): Promise<SidecarStatusResponse> {
  const qp = targetProjectRoot?.trim()
    ? `?target_project_root=${encodeURIComponent(targetProjectRoot.trim())}`
    : "";
  const res = await fetch(`${apiBaseUrl()}/threads/${encodeURIComponent(threadId)}/sidecar-status${qp}`, {
    cache: "no-store",
  });
  await parseOrThrow(res);
  return (await res.json()) as SidecarStatusResponse;
}

export async function rescanThreadBridge(
  threadId: string,
  payload: RescanBridgeRequest
): Promise<RescanBridgeResponse> {
  const res = await fetch(`${apiBaseUrl()}/threads/${encodeURIComponent(threadId)}/bridge-rescan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await parseOrThrow(res);
  return (await res.json()) as RescanBridgeResponse;
}

