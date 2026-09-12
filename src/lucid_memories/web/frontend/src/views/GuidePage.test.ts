import { describe, expect, it } from "vitest";
import {
  FAQ_ITEMS,
  FLOW_STEPS,
  GLOSSARY_ITEMS,
  filterGlossary,
} from "./GuidePage";

describe("GuidePage - Glossary & FAQ structure", () => {
  it("contains all core glossary items", () => {
    expect(GLOSSARY_ITEMS.length).toBeGreaterThanOrEqual(10);

    const ids = GLOSSARY_ITEMS.map((item) => item.id);
    expect(ids).toContain("recall");
    expect(ids).toContain("retrieval-event");
    expect(ids).toContain("memory");
    expect(ids).toContain("map");
    expect(ids).toContain("temperature");
    expect(ids).toContain("kind");
    expect(ids).toContain("scope");
    expect(ids).toContain("relay");
    expect(ids).toContain("global-persona");
  });

  it("filters glossary by keyword matching name, summary, and tags", () => {
    // Empty query returns all items
    expect(filterGlossary(GLOSSARY_ITEMS, "")).toHaveLength(GLOSSARY_ITEMS.length);
    expect(filterGlossary(GLOSSARY_ITEMS, "   ")).toHaveLength(GLOSSARY_ITEMS.length);

    // Exact or partial Japanese match
    const recallMatch = filterGlossary(GLOSSARY_ITEMS, "想起");
    expect(recallMatch.length).toBeGreaterThanOrEqual(2);
    expect(recallMatch.some((item) => item.id === "recall")).toBe(true);

    // English keyword match (case-insensitive)
    const mapMatch = filterGlossary(GLOSSARY_ITEMS, "MAP");
    expect(mapMatch.some((item) => item.id === "map")).toBe(true);

    // Tag match
    const tagMatch = filterGlossary(GLOSSARY_ITEMS, "減衰");
    expect(tagMatch.some((item) => item.id === "temperature")).toBe(true);

    // Non-existent keyword returns empty
    const noMatch = filterGlossary(GLOSSARY_ITEMS, "nonexistent-keyword-xyz");
    expect(noMatch).toHaveLength(0);
  });

  it("contains 4 flow steps and valid FAQ items", () => {
    expect(FLOW_STEPS).toHaveLength(4);
    expect(FLOW_STEPS[0].step).toBe(1);
    expect(FLOW_STEPS[3].step).toBe(4);

    expect(FAQ_ITEMS.length).toBeGreaterThanOrEqual(4);
    for (const faq of FAQ_ITEMS) {
      expect(faq.question).toBeTruthy();
      expect(faq.answer).toBeTruthy();
    }
  });
});
