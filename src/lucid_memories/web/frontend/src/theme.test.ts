// @ts-expect-error The test runner executes this fixture in Node; the app build does not bundle it.
import { readFileSync } from "node:fs";
// @ts-expect-error The test runner executes this fixture in Node; the app build does not bundle it.
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const styles = readFileSync(fileURLToPath(new URL("./styles.css", import.meta.url)), "utf8");
const themeSource = readFileSync(fileURLToPath(new URL("./theme.tsx", import.meta.url)), "utf8");
const appSource = readFileSync(fileURLToPath(new URL("./App.tsx", import.meta.url)), "utf8");

describe("theme configuration and styles", () => {
  it("defaults to dark theme and supports dark/light themes in css", () => {
    // Root and dark theme palette
    expect(styles).toMatch(/:root,\s*\[data-theme="dark"\]\s*\{[^}]*--paper:\s*#090d16/s);
    expect(styles).toMatch(/\[data-theme="light"\]\s*\{[^}]*--paper:\s*#f8fafc/s);
    
    // Default theme setting in theme.tsx is 'dark'
    expect(themeSource).toContain('return "dark"; // Default to dark theme as requested');
  });

  it("integrates Sonner toaster with resolvedTheme", () => {
    expect(appSource).toContain("<ThemedToaster />");
    expect(appSource).toContain("theme={resolvedTheme}");
    expect(appSource).toContain("richColors");
  });
});
