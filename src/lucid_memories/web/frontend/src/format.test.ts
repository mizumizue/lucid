import { describe, expect, it } from "vitest";
import { formatBytes, formatDate, formatNumber, sessionDisplayTitle, truncate } from "./format";

describe("format helpers", () => {
  it("formats numbers and truncates long values", () => {
    expect(formatNumber(1200)).toBe("1,200");
    expect(truncate("abcdef", 3)).toBe("abc…");
    expect(formatBytes(12)).toBe("12 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
  });

  it("does not expose malformed dates as markup", () => {
    expect(formatDate("<img>")).toBe("不正な日時");
  });

  it("uses the latest prompt when a session has no title", () => {
    expect(sessionDisplayTitle(null, "persona smoke\nmore details", "abcdefgh-1234")).toBe("persona smoke");
    expect(sessionDisplayTitle(null, null, "abcdefgh-1234")).toBe("Session abcdefgh…");
  });
});

