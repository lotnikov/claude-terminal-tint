#!/usr/bin/env python3
"""Tint the terminal background per Claude Code session.

Wired to the SessionStart and SessionEnd hooks so that every concurrent Claude
window carries its own background colour and is therefore easy to tell apart at
a glance.

Colour selection is a hybrid of two rules:

  * The first live session in a given directory gets the colour derived from a
    hash of that directory, so a repository keeps a recognisable colour across
    launches ("the moss-green window is access-control").
  * A second, third, ... session opened in the *same* directory would collide
    with the first, so those fall back to a hash of the session id, skipping any
    colour a live session already occupies.

Escape sequences are written straight to the terminal device rather than to
stdout, because Claude Code captures hook stdout and feeds it back into the
model's context instead of to the screen.

Requires a terminal that honours OSC 11. To check yours, run:

    printf '\033]11;#2b1b3d\007'   # tint
    printf '\033]111\033\\'        # reset
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final, Iterator, NamedTuple

# Dark, low-saturation backgrounds. Every entry sits in roughly the same
# luminance band, so a dark theme's foreground palette and Claude Code's syntax
# highlighting stay readable in all of them. On a light theme, replace these.
BACKGROUND_COLOUR_PALETTE: Final[tuple[tuple[str, str], ...]] = (
    ("plum", "#2b1b3d"),
    ("navy", "#1b2b3d"),
    ("forest", "#1b3d2b"),
    ("bronze", "#3d2b1b"),
    ("maroon", "#3d1b2b"),
    ("teal", "#12363b"),
    ("slate", "#26263d"),
    ("olive", "#33351b"),
    ("brick", "#3b2020"),
    ("moss", "#22331f"),
    ("steel", "#1a3348"),
    ("coffee", "#33291a"),
)

SESSION_REGISTRY_DIRECTORY: Final[Path] = (
    Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    / "hooks"
    / "state"
    / "terminal-bg"
)

# Linux allocates UNIX98 pseudo-terminals across these character-device majors.
PSEUDO_TERMINAL_MAJOR_RANGE: Final[range] = range(136, 144)

# Bound every process-tree walk so a malformed /proc or ps cannot spin forever.
MAXIMUM_ANCESTOR_DEPTH: Final[int] = 24


class RegisteredSession(NamedTuple):
    """One live-or-stale entry from the on-disk session registry."""

    session_id: str
    claude_process_id: int
    working_directory: str
    colour_hex: str
    colour_source: str  # "cwd" or "session"


class AncestorProcess(NamedTuple):
    """A process on the chain from this hook up to the terminal."""

    process_id: int
    command_name: str
    terminal_device_path: str | None


def _read_proc_stat(process_id: int) -> tuple[str, int, int] | None:
    """Return (command_name, parent_pid, tty_number) from /proc/<pid>/stat."""
    try:
        raw_stat_line = Path(f"/proc/{process_id}/stat").read_text()
    except OSError:
        return None

    # The comm field is parenthesised and may itself contain spaces or
    # parentheses, so split around the *last* ')' rather than tokenising blindly.
    opening_parenthesis = raw_stat_line.find("(")
    closing_parenthesis = raw_stat_line.rfind(")")
    if opening_parenthesis == -1 or closing_parenthesis == -1:
        return None

    command_name = raw_stat_line[opening_parenthesis + 1 : closing_parenthesis]
    fields_after_comm = raw_stat_line[closing_parenthesis + 2 :].split()
    if len(fields_after_comm) < 5:
        return None

    # Fields after comm are state(3) ppid(4) pgrp(5) session(6) tty_nr(7).
    try:
        return command_name, int(fields_after_comm[1]), int(fields_after_comm[4])
    except ValueError:
        return None


def terminal_device_path_from_tty_number(tty_number: int) -> str | None:
    """Turn a Linux stat(2)-style tty_nr into a /dev path."""
    if tty_number == 0:
        return None

    # /proc/<pid>/stat stores tty_nr via new_encode_dev(), which packs the low
    # 8 bits of the minor at bit 0, the major at bit 8, and the *rest* of the
    # minor from bit 20 up. Masking off 0xff after the shift is what keeps the
    # major field from leaking into the minor.
    device_major = (tty_number >> 8) & 0xFFF
    device_minor = (tty_number & 0xFF) | ((tty_number >> 12) & ~0xFF)

    if device_major in PSEUDO_TERMINAL_MAJOR_RANGE:
        pseudo_terminal_index = device_minor + (device_major - 136) * 256
        return f"/dev/pts/{pseudo_terminal_index}"
    if device_major == 4:
        return f"/dev/tty{device_minor}"
    return None


def terminal_device_path_from_ps_field(tty_field: str) -> str | None:
    """Turn a `ps -o tty=` value into a /dev path.

    Linux prints "pts/2"; macOS prints "s000" for /dev/ttys000; both print "?"
    (or "??") when the process has no controlling terminal.
    """
    if tty_field in ("?", "??", "-", ""):
        return None
    candidate_paths = (
        tty_field if tty_field.startswith("/dev/") else f"/dev/{tty_field}",
        f"/dev/tty{tty_field}",
    )
    for candidate_path in candidate_paths:
        if os.path.exists(candidate_path):
            return candidate_path
    return None


def iter_ancestors_via_proc(starting_process_id: int) -> Iterator[AncestorProcess]:
    """Walk the process tree using /proc (Linux)."""
    current_process_id = starting_process_id
    for _ in range(MAXIMUM_ANCESTOR_DEPTH):
        stat_fields = _read_proc_stat(current_process_id)
        if stat_fields is None:
            return
        command_name, parent_process_id, tty_number = stat_fields
        yield AncestorProcess(
            process_id=current_process_id,
            command_name=command_name,
            terminal_device_path=terminal_device_path_from_tty_number(tty_number),
        )
        if parent_process_id <= 1:
            return
        current_process_id = parent_process_id


def iter_ancestors_via_ps(starting_process_id: int) -> Iterator[AncestorProcess]:
    """Walk the process tree using one ps snapshot (macOS and other Unixes)."""
    try:
        completed_process = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,tty=,comm="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return

    process_table: dict[int, tuple[int, str, str]] = {}
    for output_line in completed_process.stdout.splitlines():
        line_fields = output_line.split(None, 3)
        if len(line_fields) < 4:
            continue
        try:
            process_id, parent_process_id = int(line_fields[0]), int(line_fields[1])
        except ValueError:
            continue
        # macOS prints comm as a full executable path; reduce it to a basename.
        process_table[process_id] = (
            parent_process_id,
            line_fields[2],
            os.path.basename(line_fields[3]),
        )

    current_process_id = starting_process_id
    for _ in range(MAXIMUM_ANCESTOR_DEPTH):
        table_row = process_table.get(current_process_id)
        if table_row is None:
            return
        parent_process_id, tty_field, command_name = table_row
        yield AncestorProcess(
            process_id=current_process_id,
            command_name=command_name,
            terminal_device_path=terminal_device_path_from_ps_field(tty_field),
        )
        if parent_process_id <= 1:
            return
        current_process_id = parent_process_id


def iter_ancestor_processes(starting_process_id: int) -> Iterator[AncestorProcess]:
    """Yield this process and each ancestor, nearest first, via /proc or ps."""
    if Path("/proc/self/stat").exists():
        yield from iter_ancestors_via_proc(starting_process_id)
    else:
        yield from iter_ancestors_via_ps(starting_process_id)


def process_is_alive(process_id: int) -> bool:
    """True if the process still exists (signal 0 works without /proc)."""
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Exists, owned by somebody else.
    except OSError:
        return True  # Unknown; keep the entry rather than stealing its colour.
    return True


def resolve_terminal_and_claude_process() -> tuple[str | None, int]:
    """Locate the terminal device to paint and the owning `claude` process.

    The hook's own stdout is a pipe, so the terminal has to be found by walking
    up the process tree to whichever ancestor still owns a tty. The pid of the
    `claude` ancestor doubles as the liveness token for the registry entry; it
    outlives this short-lived hook but dies with the window's session.
    """
    terminal_device_path: str | None = None
    terminal_owner_process_id: int | None = None
    claude_process_id: int | None = None

    for ancestor in iter_ancestor_processes(os.getpid()):
        if terminal_device_path is None and ancestor.terminal_device_path is not None:
            terminal_device_path = ancestor.terminal_device_path
            terminal_owner_process_id = ancestor.process_id
        if ancestor.command_name.startswith("claude"):
            claude_process_id = ancestor.process_id
            break

    if terminal_device_path is None and os.path.exists("/dev/tty"):
        terminal_device_path = "/dev/tty"

    # Prefer the `claude` process, then whatever owns the terminal, and only
    # fall back to this hook's own (immediately dying) pid as a last resort.
    liveness_process_id = (
        claude_process_id or terminal_owner_process_id or os.getpid()
    )
    return terminal_device_path, liveness_process_id


def write_to_terminal(terminal_device_path: str | None, escape_sequence: str) -> None:
    """Best-effort write of an escape sequence to the terminal device."""
    if terminal_device_path is None:
        return
    try:
        with open(terminal_device_path, "w") as terminal_device:
            terminal_device.write(escape_sequence)
            terminal_device.flush()
    except OSError:
        pass  # A closed or unwritable terminal is not worth failing a session over.


def load_registry(excluding_session_id: str) -> list[RegisteredSession]:
    """Read the registry, dropping this session and any dead ones from disk."""
    live_sessions: list[RegisteredSession] = []
    if not SESSION_REGISTRY_DIRECTORY.is_dir():
        return live_sessions

    for entry_path in SESSION_REGISTRY_DIRECTORY.glob("*.json"):
        try:
            entry_fields = json.loads(entry_path.read_text())
            registered = RegisteredSession(
                session_id=str(entry_fields["session_id"]),
                claude_process_id=int(entry_fields["claude_process_id"]),
                working_directory=str(entry_fields["working_directory"]),
                colour_hex=str(entry_fields["colour_hex"]),
                colour_source=str(entry_fields["colour_source"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            entry_path.unlink(missing_ok=True)
            continue

        if not process_is_alive(registered.claude_process_id):
            entry_path.unlink(missing_ok=True)  # Session died without SessionEnd.
            continue
        if registered.session_id == excluding_session_id:
            continue
        live_sessions.append(registered)

    return live_sessions


def stable_hash(text: str) -> int:
    """A hash that is stable across processes (unlike Python's salted hash())."""
    from hashlib import blake2b

    return int.from_bytes(blake2b(text.encode(), digest_size=8).digest(), "big")


def choose_colour(
    session_id: str, working_directory: str, live_sessions: list[RegisteredSession]
) -> tuple[str, str]:
    """Apply the hybrid rule; return (colour_hex, colour_source)."""
    directory_colour_index = stable_hash(working_directory) % len(
        BACKGROUND_COLOUR_PALETTE
    )
    directory_colour_hex = BACKGROUND_COLOUR_PALETTE[directory_colour_index][1]

    directory_colour_is_taken = any(
        registered.working_directory == working_directory
        and registered.colour_source == "cwd"
        for registered in live_sessions
    )
    if not directory_colour_is_taken:
        return directory_colour_hex, "cwd"

    # Second window in the same directory: fall back to the session id, then
    # step forward through the palette to dodge colours already on screen.
    colours_in_use = {registered.colour_hex for registered in live_sessions}
    session_colour_index = stable_hash(session_id) % len(BACKGROUND_COLOUR_PALETTE)
    for offset in range(len(BACKGROUND_COLOUR_PALETTE)):
        candidate_hex = BACKGROUND_COLOUR_PALETTE[
            (session_colour_index + offset) % len(BACKGROUND_COLOUR_PALETTE)
        ][1]
        if candidate_hex not in colours_in_use:
            return candidate_hex, "session"

    # More live sessions than palette entries; a repeat is unavoidable.
    return BACKGROUND_COLOUR_PALETTE[session_colour_index][1], "session"


def handle_session_start(session_id: str, working_directory: str) -> None:
    terminal_device_path, claude_process_id = resolve_terminal_and_claude_process()
    live_sessions = load_registry(excluding_session_id=session_id)
    colour_hex, colour_source = choose_colour(
        session_id, working_directory, live_sessions
    )

    SESSION_REGISTRY_DIRECTORY.mkdir(parents=True, exist_ok=True)
    (SESSION_REGISTRY_DIRECTORY / f"{session_id}.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "claude_process_id": claude_process_id,
                "working_directory": working_directory,
                "colour_hex": colour_hex,
                "colour_source": colour_source,
                "terminal_device_path": terminal_device_path,
            },
            indent=2,
        )
    )

    # OSC 11 sets the background colour.
    write_to_terminal(terminal_device_path, f"\033]11;{colour_hex}\007")


def handle_session_end(session_id: str) -> None:
    terminal_device_path, _ = resolve_terminal_and_claude_process()
    (SESSION_REGISTRY_DIRECTORY / f"{session_id}.json").unlink(missing_ok=True)
    # OSC 111 resets the background to the terminal profile's own colour.
    write_to_terminal(terminal_device_path, "\033]111\033\\")


def main() -> int:
    requested_mode = sys.argv[1] if len(sys.argv) > 1 else "start"

    try:
        hook_payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        hook_payload = {}

    session_id = str(hook_payload.get("session_id") or "unknown-session")
    working_directory = str(hook_payload.get("cwd") or os.getcwd())

    if requested_mode == "end":
        handle_session_end(session_id)
    else:
        handle_session_start(session_id, working_directory)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A cosmetic hook must never be the reason a session fails to start.
        sys.exit(0)
