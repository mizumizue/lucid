import { describe, expect, it } from "vitest";
import {
  contentAvailabilityLabel,
  formatBytes,
  formatCompactNumber,
  formatDate,
  formatNumber,
  formatTokenCount,
  sessionDisplayTitle,
  sessionKindLabel,
  sessionOriginLabel,
  storageScopeLabel,
  truncate,
} from "./format";

describe("format helpers", () => {
  it("formats numbers and truncates long values", () => {
    expect(formatNumber(1200)).toBe("1,200");
    expect(truncate("abcdef", 3)).toBe("abc…");
    expect(formatBytes(12)).toBe("12 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
  });

  it("shortens token magnitudes with k and M suffixes", () => {
    expect(formatCompactNumber(999)).toBe("999");
    expect(formatCompactNumber(1000)).toBe("1k");
    expect(formatCompactNumber(1200)).toBe("1.2k");
    expect(formatCompactNumber(15000)).toBe("15k");
    expect(formatCompactNumber(1_500_000)).toBe("1.5M");
    expect(formatCompactNumber(2_000_000)).toBe("2M");
    expect(formatTokenCount(1200)).toBe("1.2k tok");
    expect(formatTokenCount(850)).toBe("850 tok");
  });

  it("does not expose malformed dates as markup", () => {
    expect(formatDate("<img>")).toBe("不正な日時");
  });

  it("uses the latest prompt when a session has no title", () => {
    expect(sessionDisplayTitle(null, "persona smoke\nmore details", "abcdefgh-1234")).toBe("persona smoke");
    expect(sessionDisplayTitle(null, null, "abcdefgh-1234")).toBe("Session abcdefgh…");
    expect(sessionDisplayTitle(null, null, "abcdefgh-1234", "brief summary")).toBe("brief summary");
  });

  it("translates session metadata labels", () => {
    expect(sessionKindLabel("sub")).toBe("サブ");
    expect(sessionOriginLabel("human")).toBe("人間起点");
    expect(sessionOriginLabel("agent")).toBe("Agent起点");
  });

  it("translates availability and storage scope to friendly Japanese labels", () => {
    expect(contentAvailabilityLabel("blob")).toBe("内部Blob");
    expect(contentAvailabilityLabel("external")).toBe("外部実体ファイル");
    expect(contentAvailabilityLabel("preview")).toBe("プレビューのみ");
    expect(contentAvailabilityLabel("unavailable")).toBe("利用不可");

    expect(storageScopeLabel("conversation")).toBe("会話内Blob");
    expect(storageScopeLabel("workspace")).toBe("ワークスペース");
    expect(storageScopeLabel("repository")).toBe("リポジトリ");
  });
});

