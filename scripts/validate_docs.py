#!/usr/bin/env python3
"""Validate docs/**/*.md against .cursor/rules/docs-document-schema.mdc."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"

KINDS = {
    "needs": ("need", "NEED"),
    "actors": ("actor", "ACT"),
    "usecases": ("use_case", "UC"),
    "requirements": ("requirement", "REQ"),
    "specifications": ("specification", "SPEC"),
    "design": ("design", "DSN"),
    "decisions": ("decision", "ADR"),
    "quality": ("quality_assurance", "QA"),
    "test-cases": ("test_case", "TC"),
}

HEADINGS = {
    "need": ["### Background", "### Problem", "### Desired Outcome"],
    "actor": ["### Role", "### Responsibilities", "### Interactions"],
    "use_case": [
        "### Goal",
        "### Trigger",
        "### Preconditions",
        "### Main Flow",
        "### Alternative Flows",
        "### Postconditions",
    ],
    "requirement": ["### Statement", "### Acceptance Criteria"],
    "specification": [
        "### Contract",
        "### Inputs",
        "### Outputs",
        "### Errors",
        "### Constraints",
    ],
    "design": ["### Decision", "### Structure", "### Data Flow", "### Trade-offs"],
    "decision": ["### Context", "### Decision", "### Consequences"],
    "quality_assurance": [
        "### Objective",
        "### Quality Criteria",
        "### Verification",
        "### Evidence",
        "### Exit Criteria",
    ],
    "test_case": [
        "### Objective",
        "### Preconditions",
        "### Steps",
        "### Expected Results",
        "### Evidence",
    ],
}


def parse_simple_yaml(text: str) -> dict:
    meta: dict = {}
    lines = text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line or line.startswith("#"):
            i += 1
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val == "" and i + 1 < len(lines) and lines[i + 1].lstrip().startswith("- "):
                items = []
                i += 1
                while i < len(lines) and lines[i].lstrip().startswith("- "):
                    item = lines[i].lstrip()[2:].strip().strip("\"'")
                    items.append(item)
                    i += 1
                meta[key] = items
                continue
            elif val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                if not inner:
                    meta[key] = []
                else:
                    items = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
                    meta[key] = items
            else:
                v = val.strip("\"'")
                if v.isdigit():
                    v = int(v)
                meta[key] = v
        i += 1
    return meta


def main() -> int:
    all_files = [p for p in DOCS_DIR.glob("*/*.md") if not p.name.startswith(".")]
    print(f"Total docs files found: {len(all_files)}")

    all_ids = set()
    docs = []
    errors: list[str] = []

    for p in all_files:
        content = p.read_text(encoding="utf-8")
        parts = content.split("---")
        if len(parts) < 3:
            errors.append(f"{p}: invalid frontmatter format")
            continue
        meta = parse_simple_yaml(parts[1])
        body = "---".join(parts[2:])
        docs.append((p, meta, body))
        all_ids.add(meta.get("id"))

    for p, meta, body in docs:
        fid = meta.get("id", "")
        kind = meta.get("kind", "")
        parent_dir = p.parent.name

        if parent_dir not in KINDS:
            errors.append(f"{p}: unknown directory {parent_dir}")
            continue
        expected_kind, prefix = KINDS[parent_dir]
        if kind != expected_kind:
            errors.append(f"{p}: kind mismatch: expected {expected_kind}, got {kind}")
        if p.stem != fid:
            errors.append(f"{p}: filename stem {p.stem} != id {fid}")
        if not re.match(r"^(NEED|ACT|UC|REQ|SPEC|DSN|ADR|QA|TC)-[0-9]{4,}$", fid):
            errors.append(f"{p}: invalid id format {fid}")
        if meta.get("schema_version") != 3:
            errors.append(f"{p}: schema_version != 3 (got {meta.get('schema_version')})")
        if meta.get("status") not in [
            "draft",
            "proposed",
            "accepted",
            "rejected",
            "superseded",
            "deprecated",
        ]:
            errors.append(f"{p}: invalid status {meta.get('status')}")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(meta.get("created", ""))):
            errors.append(f"{p}: invalid created date {meta.get('created')}")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(meta.get("updated", ""))):
            errors.append(f"{p}: invalid updated date {meta.get('updated')}")

        scope = meta.get("scope")
        if scope not in ["local", "cross_cutting"]:
            errors.append(f"{p}: invalid scope {scope}")
        if scope == "cross_cutting" and kind not in ["design", "quality_assurance"]:
            errors.append(f"{p}: cross_cutting not allowed for {kind}")

        for k in ["depends_on", "tags", "links"]:
            if not isinstance(meta.get(k), list):
                errors.append(f"{p}: {k} is not a list")

        # kind-specific fields
        if kind == "use_case":
            if not isinstance(meta.get("actor_refs"), list):
                errors.append(f"{p}: use_case actor_refs is not a list")
            else:
                for aid in meta.get("actor_refs"):
                    if aid not in all_ids:
                        errors.append(f"{p}: referenced actor {aid} not found")
                    elif not aid.startswith("ACT-"):
                        errors.append(f"{p}: referenced actor {aid} has invalid prefix")
            if not isinstance(meta.get("requirement_refs"), list):
                errors.append(f"{p}: use_case requirement_refs is not a list")
            else:
                for rid in meta.get("requirement_refs"):
                    if rid not in all_ids:
                        errors.append(f"{p}: referenced requirement {rid} not found")
                    elif not rid.startswith("REQ-"):
                        errors.append(f"{p}: referenced requirement {rid} has invalid prefix")

        if kind == "test_case":
            if meta.get("test_level") not in [
                "unit",
                "integration_internal",
                "integration_external",
                "system",
                "acceptance",
            ]:
                errors.append(f"{p}: invalid test_level {meta.get('test_level')}")
            if not isinstance(meta.get("verifies"), list):
                errors.append(f"{p}: test_case verifies is not a list")
            else:
                for vid in meta.get("verifies"):
                    if vid not in all_ids:
                        errors.append(f"{p}: verified id {vid} not found")

        # depends_on direction
        deps = meta.get("depends_on", [])
        for dep in deps:
            if dep not in all_ids:
                errors.append(f"{p}: dependency {dep} not found")

        if kind in ["need", "actor", "use_case", "decision"]:
            if deps:
                errors.append(f"{p}: {kind} must have depends_on: []")
        elif kind == "requirement":
            for dep in deps:
                if not dep.startswith("NEED-"):
                    errors.append(f"{p}: requirement dependency {dep} is not NEED-")
            if len(deps) > 1:
                errors.append(f"{p}: requirement cannot depend on multiple needs (1:n relation)")
        elif kind == "specification":
            if not deps:
                errors.append(f"{p}: specification must depend on requirement")
            for dep in deps:
                if not dep.startswith("REQ-"):
                    errors.append(f"{p}: specification dependency {dep} is not REQ-")
        elif kind == "design":
            if scope != "cross_cutting" and not deps:
                errors.append(f"{p}: design must depend on specification")
            for dep in deps:
                if not dep.startswith("SPEC-"):
                    errors.append(f"{p}: design dependency {dep} is not SPEC-")
        elif kind == "quality_assurance":
            if scope != "cross_cutting" and not deps:
                errors.append(f"{p}: quality_assurance must depend on REQ- or SPEC-")
            for dep in deps:
                if not (dep.startswith("REQ-") or dep.startswith("SPEC-")):
                    errors.append(f"{p}: quality_assurance dependency {dep} is not REQ- or SPEC-")
        elif kind == "test_case":
            for dep in deps:
                if not (dep.startswith("NEED-") or dep.startswith("REQ-") or dep.startswith("SPEC-") or dep.startswith("DSN-")):
                    errors.append(f"{p}: test_case dependency {dep} is not NEED-, REQ-, SPEC-, or DSN-")

        # links existence
        for lid in meta.get("links", []):
            if lid not in all_ids:
                errors.append(f"{p}: link target {lid} not found")

        # headings
        content_matches = re.findall(r"^## Content\s*$", body, re.M)
        if len(content_matches) != 1:
            errors.append(f"{p}: expected exactly one ## Content, found {len(content_matches)}")

        exp_h = HEADINGS.get(kind, [])
        found_h = re.findall(r"^### [^\n]+", body, re.M)
        if found_h != exp_h:
            errors.append(f"{p}: heading mismatch. Expected {exp_h}, got {found_h}")

    if errors:
        print(f"FAIL: {len(errors)} errors found:")
        for e in errors:
            print("  -", e)
        return 1
    else:
        print(f"PASS: All {len(docs)} docs files strictly follow docs-document-schema.mdc!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
