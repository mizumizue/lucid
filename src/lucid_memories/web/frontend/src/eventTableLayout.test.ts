// @ts-expect-error The test runner executes this fixture in Node; the app build does not bundle it.
import { readFileSync } from "node:fs";
// @ts-expect-error The test runner executes this fixture in Node; the app build does not bundle it.
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const styles = readFileSync(fileURLToPath(new URL("./styles.css", import.meta.url)), "utf8");
const activityPage = readFileSync(fileURLToPath(new URL("./views/ActivityPage.tsx", import.meta.url)), "utf8");

describe("conversation event table layout", () => {
  it("uses a constrained table and expandable text that cannot widen its columns", () => {
    expect(activityPage).toMatch(/className="data-table event-table"/);
    expect(styles).toMatch(/\.event-table\s*\{[^}]*table-layout:\s*fixed/s);
    expect(styles).toMatch(/\.event-table\s+\.expandable-text\s*\{[^}]*min-width:\s*0/s);
    expect(styles).toMatch(/\.event-table\s+\.truncate\s*\{[^}]*display:\s*block/s);
  });

  it("uses consistent filter-panel and filter-row for period controls instead of inline controls", () => {
    expect(activityPage).toMatch(/<Panel className="filter-panel">\s*<div className="filter-row">/);
    expect(activityPage).not.toMatch(/className="view-controls"/);
    expect(activityPage).not.toMatch(/className="date-controls"/);
  });
});
