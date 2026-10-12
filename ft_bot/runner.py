"""Invoke `claude -p` and keep every stdout line in logs/runs/NNNNNN.jsonl."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from ft_bot.config import claude_bin, claude_timeout_s, logs_dir, repo_root, runs_dir, workspace_dir
from ft_bot.trace import ParsedRun, parse_stream

log = logging.getLogger(__name__)

# Bash is not offered. Read-only shell commands are auto-approved in dontAsk
# and can print the environment. The bot pre-parses attachments.
_TOOLS = "Read,Write,Edit,Glob,Grep"
_ENV_PASSTHROUGH = (
    "PATH",
    "HOME",
    "LANG",
    "TZ",
    "AGENT_DATABASE_URL",
    "MIGRATOR_DATABASE_URL",
)


@dataclass
class ClaudeRun:
    returncode: int
    stderr: str
    parsed: ParsedRun
    session_mode: str
    timed_out: bool


def mcp_config_path() -> Path:
    return repo_root() / "bot" / "agent" / "mcp.json"


def harness_settings_path() -> Path:
    return logs_dir() / "harness-settings.json"


def harness_settings(workspace: Path | None = None) -> dict:
    """Deny rules live outside the cwd so the agent cannot rewrite them."""
    root = (workspace or workspace_dir()).resolve()
    repo = repo_root().resolve()
    read_workspace = _abs_rule("Read", root, "/**")
    edit_memory = _abs_rule("Edit", root / "MEMORY.md")
    edit_categories = _abs_rule("Edit", root / "CATEGORIES.md")
    write_memory = _abs_rule("Write", root / "MEMORY.md")
    write_categories = _abs_rule("Write", root / "CATEGORIES.md")
    return {
        "autoMemoryEnabled": False,
        "permissions": {
            "allow": [
                read_workspace,
                edit_memory,
                edit_categories,
                write_memory,
                write_categories,
                "Glob",
                "Grep",
                "mcp__finance__*",
            ],
            "deny": [
                "Bash",
                _abs_rule("Edit", root / "CLAUDE.md"),
                _abs_rule("Write", root / "CLAUDE.md"),
                _abs_rule("Edit", root / ".claude", "/**"),
                _abs_rule("Write", root / ".claude", "/**"),
                _abs_rule("Edit", root / "mcp.json"),
                _abs_rule("Write", root / "mcp.json"),
                _abs_rule("Edit", repo / "db", "/**"),
                _abs_rule("Write", repo / "db", "/**"),
                _abs_rule("Edit", repo / ".git", "/**"),
                _abs_rule("Write", repo / ".git", "/**"),
                "Read(//proc/**)",
                _abs_rule("Read", repo / "db", "/**"),
                _abs_rule("Read", repo / "bot", "/**"),
                _abs_rule("Read", repo / "ft_bot", "/**"),
                _abs_rule("Read", repo / "ft_mcp", "/**"),
                _abs_rule("Read", repo / "ft_db", "/**"),
                _abs_rule("Read", repo / "logs", "/**"),
                _abs_rule("Read", repo / ".git", "/**"),
            ],
        },
    }


def allowed_tools(workspace: Path | None = None) -> str:
    root = (workspace or workspace_dir()).resolve()
    return ",".join([
        _abs_rule("Read", root, "/**"),
        _abs_rule("Edit", root / "MEMORY.md"),
        _abs_rule("Edit", root / "CATEGORIES.md"),
        _abs_rule("Write", root / "MEMORY.md"),
        _abs_rule("Write", root / "CATEGORIES.md"),
        "Glob",
        "Grep",
        "mcp__finance__*",
    ])


def write_harness_settings() -> Path:
    path = harness_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(harness_settings(), indent=2) + "\n")
    return path


def _abs_rule(tool: str, path: Path, suffix: str = "") -> str:
    """`//` is Claude Code's absolute-path form: `/app/db` becomes `//app/db`."""
    text = path.resolve().as_posix()
    if not text.startswith("/"):
        text = "/" + text
    return f"{tool}(/{text}{suffix})"


STREAM_LINE_LIMIT = 64 * 1024 * 1024


def build_command(prompt: str, session_id: str, session_mode: str) -> list[str]:
    """Flags from the ticket. `--permission-mode dontAsk` keeps headless from blocking.

    `dontAsk` denies anything that is not in `--allowedTools` instead of waiting
    for a person. `--bare` is intentionally absent: it cannot use the subscription.
    Settings and mcp.json are outside the workspace.
    """
    command = [
        claude_bin(),
        "-p",
        prompt,
        "--setting-sources",
        "project",
        "--settings",
        str(harness_settings_path()),
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config_path()),
        "--tools",
        _TOOLS,
        "--allowedTools",
        allowed_tools(),
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
    ]
    if session_mode == "resume":
        command.extend(["--resume", session_id])
    else:
        command.extend(["--session-id", session_id])
    return command


def child_env(run_id: int) -> dict[str, str]:
    """Allowlist for the claude process. The superuser URL never crosses."""
    env: dict[str, str] = {}
    for key in _ENV_PASSTHROUGH:
        value = os.environ.get(key, "").strip()
        if value:
            env[key] = value
    for key, value in os.environ.items():
        if key.startswith("CLAUDE_CODE_") and value:
            env[key] = value
        elif key.startswith("FT_FAKE_") and value:
            env[key] = value
    env["APP_RUN_ID"] = str(run_id)
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env.setdefault("TZ", "Asia/Kolkata")
    venv_bin = repo_root() / ".venv" / "bin"
    if venv_bin.is_dir():
        env["PATH"] = str(venv_bin) + os.pathsep + env.get("PATH", "")
    return env


def build_prompt(incoming, seq: int) -> str:
    folder = f"messages/{seq:06d}"
    if incoming.prompt_override:
        return incoming.prompt_override.format(seq=f"{seq:06d}", folder=folder)
    if incoming.synthetic_action == "edit" and incoming.synthetic_target_seq:
        target = f"{incoming.synthetic_target_seq:06d}"
        return (
            f"A new user message is in {folder}/. "
            f"The user pressed Edit on message {target}. "
            f"Read {folder}/message.md and ask what they want to change."
        )
    return (
        f"A new user message is in {folder}/. "
        f"Read {folder}/message.md and the files it names, including parsed siblings, then handle it."
    )


async def run_claude(
    *,
    prompt: str,
    session_id: str,
    session_mode: str,
    run_id: int,
    log_path: Path,
) -> ClaudeRun:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_command(prompt, session_id, session_mode)
    log.info(
        "claude seq_log=%s run_id=%s mode=%s",
        log_path.name,
        run_id,
        session_mode,
    )
    write_harness_settings()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(workspace_dir()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env(run_id),
        start_new_session=True,
        # stream-json lines can carry whole images (base64) from Read; asyncio's
        # default 64 KiB line limit kills the run on a screenshot.
        limit=STREAM_LINE_LIMIT,
    )
    stdout_task = asyncio.create_task(_pump(process.stdout, log_path))
    stderr_task = asyncio.create_task(_read_all(process.stderr))
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=claude_timeout_s())
    except TimeoutError:
        timed_out = True
        _kill_group(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
    raw_stdout = await _bounded(stdout_task, timed_out)
    stderr = (await _bounded(stderr_task, timed_out)).decode("utf-8", errors="replace")
    parsed = parse_stream(raw_stdout.decode("utf-8", errors="replace"))
    returncode = process.returncode if process.returncode is not None else 1
    if timed_out or returncode != 0:
        parsed.is_error = True
        if not parsed.result_text.strip():
            tail = _public_stderr(stderr)
            parsed.result_text = tail or ("timed out" if timed_out else "claude exited without a result")
    return ClaudeRun(
        returncode=returncode,
        stderr=stderr,
        parsed=parsed,
        session_mode=session_mode,
        timed_out=timed_out,
    )


def log_file_for(seq: int) -> Path:
    return runs_dir() / f"{seq:06d}.jsonl"


def relative_log_path(seq: int) -> str:
    return f"logs/runs/{seq:06d}.jsonl"


def _kill_group(process: asyncio.subprocess.Process) -> None:
    if process.pid is None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        process.kill()


async def _bounded(task: asyncio.Task, timed_out: bool) -> bytes:
    if not timed_out:
        return await task
    try:
        return await asyncio.wait_for(task, timeout=5)
    except TimeoutError:
        task.cancel()
        return b""


async def _pump(stream: asyncio.StreamReader | None, path: Path) -> bytes:
    """Copy the child's stdout to the run log. Reads fixed-size chunks rather than
    lines, so a single huge stream-json event (e.g. an image) can never abort the run."""
    chunks: list[bytes] = []
    with path.open("wb") as handle:
        if stream is None:
            return b""
        while True:
            chunk = await stream.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            handle.flush()
            chunks.append(chunk)
    return b"".join(chunks)


async def _read_all(stream: asyncio.StreamReader | None) -> bytes:
    if stream is None:
        return b""
    return await stream.read()


def _public_stderr(stderr: str) -> str:
    kept: list[str] = []
    for line in stderr.splitlines():
        upper = line.upper()
        if any(word in upper for word in ("TOKEN", "API_KEY", "PASSWORD", "SECRET")):
            continue
        if line.strip():
            kept.append(line.strip())
    if not kept:
        return ""
    return kept[-1][:500]
