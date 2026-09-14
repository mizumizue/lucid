export function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat("ja-JP").format(Number(value || 0));
}

export function formatCompactNumber(value: number | null | undefined): string {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0";
  const abs = Math.abs(n);
  if (abs < 1000) return formatNumber(n);
  const [div, suffix] = abs < 1_000_000 ? [1_000, "k"] as const : [1_000_000, "M"] as const;
  const compact = n / div;
  const rounded = Math.abs(compact) >= 10 ? compact.toFixed(0) : compact.toFixed(1);
  return `${Number(rounded)}${suffix}`;
}

export function formatTokenCount(value: number | null | undefined): string {
  return `${formatCompactNumber(value)} tok`;
}

export function formatCost(value: number | null | undefined): string {
  return `$${Number(value || 0).toFixed(4)}`;
}

export function formatDate(value: string | null | undefined, withTime = false): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "不正な日時";
  return withTime
    ? date.toLocaleString("ja-JP", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : date.toLocaleDateString("ja-JP", { month: "numeric", day: "numeric" });
}

export function shortId(value: string | null | undefined): string {
  return value ? `${value.slice(0, 8)}…` : "—";
}

export function sessionDisplayTitle(
  title: string | null | undefined,
  lastPrompt: string | null | undefined,
  conversationId: string | null | undefined,
  brief?: string | null,
): string {
  if (title?.trim()) return title.trim();
  const prompt = brief?.split(/\r?\n/, 1)[0].trim()
    || lastPrompt?.split(/\r?\n/, 1)[0].trim();
  if (prompt) return truncate(prompt, 80);
  return conversationId ? `Session ${shortId(conversationId)}` : "Session";
}

export function sessionKindLabel(value: string | null | undefined): string {
  return (
    {
      main: "メイン",
      sub: "サブ",
      background: "BG",
    } as Record<string, string>
  )[value || ""] || "不明";
}

export function sessionOriginLabel(value: string | null | undefined): string {
  return (
    {
      human: "人間起点",
      agent: "Agent起点",
      unknown: "起点不明",
    } as Record<string, string>
  )[value || ""] || "起点不明";
}

export function sessionKindClass(value: string | null | undefined): string {
  return (
    {
      main: "status-pill-indigo",
      sub: "status-pill-yellow",
      background: "status-pill-coral",
    } as Record<string, string>
  )[value || ""] || "";
}

export function sessionOriginClass(value: string | null | undefined): string {
  return (
    {
      human: "status-pill-mint",
      agent: "status-pill-indigo",
      unknown: "",
    } as Record<string, string>
  )[value || ""] || "";
}

export function formatBytes(value: number | null | undefined): string {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function contentAvailabilityLabel(value: string | null | undefined): string {
  return (
    {
      blob: "内部Blob",
      external: "外部実体ファイル",
      preview: "プレビューのみ",
      unavailable: "利用不可",
    } as Record<string, string>
  )[value || ""] || value || "不明";
}

export function statusClass(value: string | null | undefined): string {
  return ["done", "error", "stale"].includes(value || "") ? value || "" : "";
}

export function storageScopeLabel(value: string | null | undefined): string {
  return (
    {
      conversation: "会話内Blob",
      workspace: "ワークスペース",
      repository: "リポジトリ",
    } as Record<string, string>
  )[value || ""] || value || "成果物";
}

export function truncate(value: string | null | undefined, length = 140): string {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

