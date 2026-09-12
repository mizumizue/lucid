"""Privacy and sensitive information gate for Git and command execution."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

SENSITIVE_PATH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:^|[/\\])persona[/\\][^/\\]+\.json$", re.IGNORECASE),
        "Persona / user rules file containing private preferences",
    ),
    (
        re.compile(r"(?:^|[/\\])\.env(?:\.[^/\\]+)?$", re.IGNORECASE),
        "Environment credential file",
    ),
    (
        re.compile(r"(?:^|[/\\])(?:id_rsa|id_ed25519|id_ecdsa)(?:[._-][^/\\]*)?$", re.IGNORECASE),
        "SSH private key file",
    ),
    (
        re.compile(r"\.(?:pem|key|pkcs12|pfx|p12)$", re.IGNORECASE),
        "Cryptographic private key or certificate file",
    ),
    (
        re.compile(r"(?:^|[/\\])[^/\\]*(?:credentials|token|client_secret)[^/\\]*\.json$", re.IGNORECASE),
        "OAuth / API credential token file",
    ),
    (
        re.compile(r"(?:^|[/\\])(?:local_settings\.py|.*\.local\.[^/\\]+)$", re.IGNORECASE),
        "Local override / secret settings file",
    ),
]

SAFE_PATH_ALLOWLIST: list[re.Pattern[str]] = [
    re.compile(r"\.env\.example$", re.IGNORECASE),
    re.compile(r"\.env\.template$", re.IGNORECASE),
    re.compile(r"\.gitkeep$", re.IGNORECASE),
]

# Patterns for scanning staged content for local personal info (e.g. absolute user home paths)
CONTENT_PERSONAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"[a-zA-Z]:\\(?:Users|Documents and Settings)\\[^\\\s\"'>]+", re.IGNORECASE),
        "Windows user home absolute path",
    ),
    (
        re.compile(r"/(?:Users|home)/[^\s/\"'>]+", re.IGNORECASE),
        "Unix user home absolute path",
    ),
]


def is_safe_path(path_str: str) -> bool:
    norm = path_str.replace("\\", "/")
    return any(p.search(norm) for p in SAFE_PATH_ALLOWLIST)


def check_sensitive_path(path_str: str) -> tuple[bool, str]:
    norm = path_str.replace("\\", "/")
    if is_safe_path(norm):
        return False, ""
    for pattern, desc in SENSITIVE_PATH_PATTERNS:
        if pattern.search(norm):
            return True, desc
    return False, ""


def is_local_only_repo(repo_root: Path | None = None) -> bool:
    """Return True if repository has no remote configured or is explicitly local-only."""
    if os.environ.get("LUCID_LOCAL_ONLY_REPO", "").lower() in {"1", "true", "yes"}:
        return True

    cwd = repo_root or Path.cwd()
    try:
        # Check git config
        cfg = subprocess.run(
            ["git", "config", "--bool", "lucid.localOnly"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if cfg.returncode == 0 and cfg.stdout.strip().lower() == "true":
            return True

        # Check remotes
        res = subprocess.run(
            ["git", "remote"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            # Not a git repo or git error -> treat as local-only
            return True
        remotes = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        return len(remotes) == 0
    except (OSError, subprocess.SubprocessError):
        return True


def check_staged_files(repo_root: Path | None = None, repo_path: Path | None = None) -> dict[str, Any]:
    """Inspect staged files for sensitive paths and private local info."""
    cwd = repo_root or repo_path or Path.cwd()
    if is_local_only_repo(cwd):
        return {
            "ok": True,
            "local_only": True,
            "message": "Local-only repository; privacy checks skipped.",
            "violations": [],
        }

    try:
        staged_res = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        if staged_res.returncode != 0:
            return {"ok": True, "message": "Not a git repo or no commits yet.", "violations": []}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": True, "error": str(exc), "violations": []}

    staged_files = [line.strip() for line in staged_res.stdout.splitlines() if line.strip()]
    if not staged_files:
        return {"ok": True, "staged_count": 0, "violations": []}

    violations: list[dict[str, str]] = []
    for file_path in staged_files:
        sensitive, reason = check_sensitive_path(file_path)
        if sensitive:
            violations.append(
                {
                    "type": "file_path",
                    "file": file_path,
                    "reason": reason,
                }
            )

    # Check diff content for local absolute paths
    try:
        diff_res = subprocess.run(
            ["git", "diff", "--cached", "-U0"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        if diff_res.returncode == 0:
            added_lines = [
                line[1:]
                for line in diff_res.stdout.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            ]
            diff_text = "\n".join(added_lines)
            for pattern, desc in CONTENT_PERSONAL_PATTERNS:
                matches = pattern.findall(diff_text)
                if matches:
                    violations.append(
                        {
                            "type": "content_pattern",
                            "file": "staged_diff",
                            "reason": f"{desc} detected in staged diff: {matches[0]}",
                        }
                    )
    except (OSError, subprocess.SubprocessError):
        pass

    return {
        "ok": len(violations) == 0,
        "local_only": False,
        "staged_count": len(staged_files),
        "violations": violations,
    }


def main() -> int:
    import sys

    result = check_staged_files()
    if result["ok"]:
        if result.get("local_only"):
            sys.stdout.write("[privacy-hook] Notice: Local-only repo; git privacy check skipped.\n")
        else:
            sys.stdout.write("[privacy-hook] Privacy check passed: no personal/sensitive files staged.\n")
        return 0

    sys.stderr.write("[privacy-hook:BLOCKED] Git commit aborted! Sensitive/personal information detected:\n")
    for v in result.get("violations", []):
        sys.stderr.write(f"  - [{v.get('type')}] {v.get('file')}: {v.get('reason')}\n")
    sys.stderr.write("\nTo fix this:\n")
    sys.stderr.write("  1. Remove sensitive files from staging: git reset HEAD <file>\n")
    sys.stderr.write("  2. Add sensitive files to .gitignore\n")
    sys.stderr.write("  3. If this is strictly a local-only repository, run: git config lucid.localOnly true\n")
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
