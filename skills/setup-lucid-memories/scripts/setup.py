#!/usr/bin/env python3
"""Cross-platform setup and rebuild script for lucid-memories.

Configures:
1. Python dependencies (ladybug)
2. Storage & database directory (~/.cursor/lucid-memories/.db)
3. Cursor MCP configuration (~/.cursor/mcp.json)
4. Cursor Hooks configuration (~/.cursor/hooks.json & ~/.cursor/hooks/lucid-memories.*)
5. Personal / Cursor Skills (~/.cursor/skills/)
6. Persona templates (~/.cursor/lucid-memories/persona/)
7. Verification / Self-test (cli status)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MIN_PYTHON = (3, 10)
REQUIRED_DEPENDENCIES = ["ladybug>=0.20.0"]
HOOK_EVENTS = [
    ("sessionStart", 10),
    ("beforeSubmitPrompt", 10),
    ("preToolUse", 10),
    ("postToolUse", 10),
    ("postToolUseFailure", 10),
    ("beforeMCPExecution", 10),
    ("beforeShellExecution", 10),
    ("afterShellExecution", 10),
    ("afterMCPExecution", 10),
    ("afterFileEdit", 10),
    ("subagentStart", 10),
    ("subagentStop", 10),
    ("preCompact", 15),
    ("stop", 10),
    ("afterAgentResponse", 10),
    ("afterAgentThought", 10),
    ("sessionEnd", 10),
]


def log(msg: str) -> None:
    sys.stdout.write(f"[setup] {msg}\n")


def warn(msg: str) -> None:
    sys.stderr.write(f"[setup:warn] {msg}\n")


def err(msg: str) -> None:
    sys.stderr.write(f"[setup:error] {msg}\n")


def check_python_version() -> None:
    if sys.version_info < MIN_PYTHON:
        err(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, current is {sys.version}")
        sys.exit(1)
    log(f"Python version: {sys.version.split()[0]} (OK)")


def install_dependencies(repo_root: Path, skip_pip: bool = False) -> None:
    if skip_pip:
        log("Skipping pip dependency installation (--skip-pip)")
        return
    log("Installing Python dependencies (editable mode)...")
    cmd = [sys.executable, "-m", "pip", "install", "-e", str(repo_root)]
    subprocess.check_call(cmd)
    log("Dependencies installed successfully.")


def ensure_repo_placement(repo_root: Path) -> Path:
    cursor_home = Path.home() / ".cursor"
    expected_path = cursor_home / "lucid-memories"
    cursor_home.mkdir(parents=True, exist_ok=True)

    if repo_root.resolve() == expected_path.resolve():
        log(f"Repository is correctly located at {expected_path}")
        return expected_path

    # If repository is cloned elsewhere, create symlink or ensure agent-bus link
    log(f"Repository is located at {repo_root} (expected: {expected_path})")
    if not expected_path.exists():
        try:
            expected_path.symlink_to(repo_root, target_is_directory=True)
            log(f"Created symlink: {expected_path} -> {repo_root}")
        except OSError:
            warn(f"Could not create symlink at {expected_path}. Using repo path {repo_root} directly.")
            return repo_root
    return expected_path


def clean_legacy_directories(repo_root: Path) -> None:
    for name in ("blobs", "logs"):
        legacy_dir = repo_root / name
        if legacy_dir.is_dir():
            files = [p for p in legacy_dir.rglob("*") if p.is_file()]
            if not files:
                try:
                    shutil.rmtree(legacy_dir)
                    log(f"Removed empty legacy directory: {legacy_dir}")
                except OSError as e:
                    warn(f"Could not remove empty legacy directory {legacy_dir}: {e}")
            else:
                warn(f"Legacy directory {legacy_dir} contains files; leaving intact.")


def ensure_directories(repo_root: Path) -> None:
    cursor_home = Path.home() / ".cursor"
    (cursor_home / "hooks").mkdir(parents=True, exist_ok=True)
    (cursor_home / "skills").mkdir(parents=True, exist_ok=True)
    (repo_root / ".db").mkdir(parents=True, exist_ok=True)
    (repo_root / "persona").mkdir(parents=True, exist_ok=True)
    clean_legacy_directories(repo_root)
    log("Required directories verified.")


def configure_mcp(repo_root: Path, python_exe: str) -> None:
    mcp_path = Path.home() / ".cursor" / "mcp.json"
    mcp_server_script = repo_root / "src" / "lucid_memories" / "entrypoints" / "mcp_server.py"

    data: dict[str, Any] = {}
    if mcp_path.exists():
        try:
            with open(mcp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            warn(f"Failed to parse existing mcp.json: {e}. Backing up to mcp.json.bak")
            shutil.copy2(mcp_path, mcp_path.with_suffix(".json.bak"))
            data = {}

    servers = data.setdefault("mcpServers", {})
    servers["lucid-memories"] = {
        "command": python_exe,
        "args": [str(mcp_server_script)],
        "env": {
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    }

    with open(mcp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    log(f"MCP server configured in {mcp_path}")


def configure_hooks(repo_root: Path, python_exe: str) -> None:
    cursor_home = Path.home() / ".cursor"
    hooks_dir = cursor_home / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_entry = repo_root / "src" / "lucid_memories" / "entrypoints" / "hook.py"

    is_win = sys.platform == "win32"
    if is_win:
        cmd_script = hooks_dir / "lucid-memories.cmd"
        content = (
            "@echo off\n"
            "setlocal\n"
            "set \"PYTHONUTF8=1\"\n"
            "set \"PYTHONIOENCODING=utf-8\"\n"
            f"set \"PY={python_exe}\"\n"
            f"set \"HOOK={hook_entry}\"\n"
            "if exist \"%PY%\" (\n"
            "  \"%PY%\" \"%HOOK%\" %*\n"
            ") else (\n"
            "  python \"%HOOK%\" %*\n"
            ")\n"
            "if errorlevel 1 (\n"
            "  echo {}\n"
            "  exit /b 0\n"
            ")\n"
            "exit /b 0\n"
        )
        cmd_script.write_text(content, encoding="utf-8")
        hook_cmd_target = str(cmd_script)
        log(f"Generated hook script: {cmd_script}")
    else:
        sh_script = hooks_dir / "lucid-memories.sh"
        content = (
            "#!/usr/bin/env bash\n"
            "export PYTHONUTF8=1\n"
            "export PYTHONIOENCODING=utf-8\n"
            f"PY=\"{python_exe}\"\n"
            f"HOOK=\"{hook_entry}\"\n"
            "if [ -x \"$PY\" ]; then\n"
            "  \"$PY\" \"$HOOK\" \"$@\"\n"
            "else\n"
            "  python3 \"$HOOK\" \"$@\"\n"
            "fi\n"
            "rc=$?\n"
            "if [ $rc -ne 0 ]; then\n"
            "  echo '{}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        )
        sh_script.write_text(content, encoding="utf-8")
        sh_script.chmod(0o755)
        hook_cmd_target = str(sh_script)
        log(f"Generated hook script: {sh_script}")

    hooks_json_path = cursor_home / "hooks.json"
    data: dict[str, Any] = {}
    if hooks_json_path.exists():
        try:
            with open(hooks_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            warn(f"Failed to parse existing hooks.json: {e}. Backing up to hooks.json.bak")
            shutil.copy2(hooks_json_path, hooks_json_path.with_suffix(".json.bak"))
            data = {}

    data.setdefault("version", 1)
    hooks_map = data.setdefault("hooks", {})

    for event_name, timeout in HOOK_EVENTS:
        existing = hooks_map.setdefault(event_name, [])
        # Check if lucid-memories hook already present
        present = False
        for entry in existing:
            cmd = entry.get("command", "")
            if "lucid-memories" in cmd:
                entry["command"] = hook_cmd_target
                entry["timeout"] = timeout
                present = True
                break
        if not present:
            existing.append({"command": hook_cmd_target, "timeout": timeout})

    with open(hooks_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    log(f"Hooks configuration updated in {hooks_json_path}")


def deploy_skills(repo_root: Path) -> None:
    source_skills = repo_root / "skills"
    if not source_skills.exists():
        log("No bundled skills/ directory found; skipping skill deployment.")
        return

    dest_skills = Path.home() / ".cursor" / "skills"
    dest_skills.mkdir(parents=True, exist_ok=True)

    for item in source_skills.iterdir():
        if item.is_dir() and (item / "SKILL.md").exists():
            target_dir = dest_skills / item.name
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item / "SKILL.md", target_dir / "SKILL.md")
            scripts_dir = item / "scripts"
            if scripts_dir.is_dir():
                target_scripts = target_dir / "scripts"
                target_scripts.mkdir(parents=True, exist_ok=True)
                for script_file in scripts_dir.iterdir():
                    if script_file.is_file():
                        shutil.copy2(script_file, target_scripts / script_file.name)
            log(f"Deployed skill '{item.name}' -> {target_dir / 'SKILL.md'}")


def configure_bin_scripts(repo_root: Path) -> None:
    source_bin = repo_root / "bin"
    if not source_bin.exists():
        return
    target_bin = Path.home() / "bin"
    target_bin.mkdir(parents=True, exist_ok=True)
    for item in source_bin.iterdir():
        if item.is_file():
            target_path = target_bin / item.name
            shutil.copy2(item, target_path)
            if not item.name.endswith(".cmd"):
                try:
                    target_path.chmod(target_path.stat().st_mode | 0o755)
                except OSError:
                    pass
            log(f"Deployed CLI launcher: {target_path}")


def ensure_persona(repo_root: Path) -> None:
    persona_dir = repo_root / "persona"
    persona_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = persona_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()

    now_str = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    user_rules_json = persona_dir / "user-rules.json"
    if not user_rules_json.exists():
        default_user_rules = {
            "schema_version": 1,
            "source": "cursor-user-rules",
            "scope": "user",
            "workspace_root": None,
            "extracted_at": now_str,
            "rules": [],
        }
        with open(user_rules_json, "w", encoding="utf-8") as f:
            json.dump(default_user_rules, f, ensure_ascii=False, indent=2)
            f.write("\n")
        log(f"Initialized user-specific rules: {user_rules_json}")
    else:
        log(f"Existing user-rules found (preserved): {user_rules_json}")

    persona_json = persona_dir / "persona.json"
    if not persona_json.exists():
        src_path = repo_root / "src"
        if str(src_path) not in sys.path:
            sys.path.insert(0, str(src_path))
        from lucid_memories.core import persona as persona_mod

        raw_rules = json.loads(user_rules_json.read_text(encoding="utf-8"))
        built = persona_mod.build_persona(raw_rules)
        with open(persona_json, "w", encoding="utf-8") as f:
            json.dump(built, f, ensure_ascii=False, indent=2)
            f.write("\n")
        log(f"Generated user-specific persona from rules: {persona_json}")
    else:
        log(f"Existing persona found (preserved): {persona_json}")

    types_dir = persona_dir / "types"
    types_dir.mkdir(parents=True, exist_ok=True)
    types_gitkeep = types_dir / ".gitkeep"
    if not types_gitkeep.exists():
        types_gitkeep.touch()
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    from lucid_memories.core import workspace_persona as workspace_persona_mod

    workspace_persona_mod.ensure_type_templates()
    log(f"Ensured workspace persona type templates under: {types_dir}")


def probe_ollama() -> None:
    url = "http://127.0.0.1:11434/api/tags"
    req = urllib.request.Request(url, headers={"User-Agent": "lucid-memories-setup"})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            log("Ollama daemon is reachable.")
            if any("nomic-embed-text" in m for m in models):
                log("Embedding model 'nomic-embed-text' found in Ollama (Vector search ready).")
            else:
                log("Notice: 'nomic-embed-text' not found in Ollama. Run: ollama pull nomic-embed-text")
    except Exception:
        log("Notice: Ollama is not reachable on localhost:11434. Fulltext search (SQLite FTS5) will be used as fallback.")


def verify_installation(repo_root: Path) -> None:
    log("Verifying lucid-memories installation...")
    cli_path = repo_root / "src" / "lucid_memories" / "entrypoints" / "cli.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src") + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
    try:
        out = subprocess.check_output(
            [sys.executable, str(cli_path), "status"],
            env=env,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
        status = json.loads(out)
        if status.get("ok"):
            log("Verification SUCCESS: CLI status returned ok: true.")
        else:
            warn(f"Verification output returned non-ok status: {out}")
    except Exception as e:
        warn(f"Self-test verification warning: {e}")

    try:
        persona_out = subprocess.check_output(
            [sys.executable, str(cli_path), "persona", "validate"],
            env=env,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
        persona_status = json.loads(persona_out)
        if persona_status.get("ok"):
            log("Verification SUCCESS: Persona validation returned ok: true.")
        else:
            warn(f"Persona validation warning: {persona_out}")
    except Exception as e:
        warn(f"Persona verification warning: {e}")


def find_repo_root() -> Path:
    current = Path(__file__).resolve()
    # If in <repo_root>/skills/setup-lucid-memories/scripts/setup.py
    if len(current.parents) >= 4:
        candidate = current.parents[3]
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "lucid_memories").exists():
            return candidate

    # Search upwards from current directory
    for parent in current.parents:
        if (parent / "pyproject.toml").exists() and (parent / "src" / "lucid_memories").exists():
            return parent

    # Fallback to standard location ~/.cursor/lucid-memories
    standard_path = Path.home() / ".cursor" / "lucid-memories"
    if standard_path.exists() and (standard_path / "pyproject.toml").exists():
        return standard_path

    # Fallback to current working directory
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "src" / "lucid_memories").exists():
        return cwd

    # Last resort fallback to 3 parents up
    return current.parents[3] if len(current.parents) >= 4 else current.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup and wire lucid-memories into Cursor")
    parser.add_argument("--skip-pip", action="store_true", help="Skip pip installation of dependencies")
    parser.add_argument("--skip-verify", action="store_true", help="Skip self-test verification")
    parser.add_argument("--python", default=sys.executable, help="Path to python executable to wire into Cursor")
    args = parser.parse_args()

    repo_root = find_repo_root()
    log(f"Starting lucid-memories rebuild setup from {repo_root}...")

    check_python_version()
    ensure_repo_placement(repo_root)
    ensure_directories(repo_root)
    install_dependencies(repo_root, skip_pip=args.skip_pip)
    ensure_persona(repo_root)
    configure_mcp(repo_root, python_exe=args.python)
    configure_hooks(repo_root, python_exe=args.python)
    deploy_skills(repo_root)
    configure_bin_scripts(repo_root)
    probe_ollama()

    if not args.skip_verify:
        verify_installation(repo_root)

    log("lucid-memories setup complete! Restart Cursor or reload window to activate MCP & Hooks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
