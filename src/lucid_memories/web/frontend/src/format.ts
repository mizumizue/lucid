export function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat("ja-JP").format(Number(value || 0));
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
): string {
  if (title?.trim()) return title.trim();
  const prompt = lastPrompt?.split(/\r?\n/, 1)[0].trim();
  if (prompt) return truncate(prompt, 80);
  return conversationId ? `Session ${shortId(conversationId)}` : "Session";
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

