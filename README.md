# terminal-tint

A Claude Code plugin that gives every concurrent Claude window its own terminal
background colour, so you can tell at a glance which window is which task.

## Install

```shell
claude plugin marketplace add lotnikov/claude-terminal-tint
claude plugin install terminal-tint@slotnikov-tools
```

To try it straight from a local clone instead:

```shell
claude plugin marketplace add /path/to/claude-terminal-tint
claude plugin install terminal-tint@slotnikov-tools
```

Then open a new terminal — hooks are loaded at session start, so the session you
install from is not tinted.

## How the colour is chosen

- The first live session in a directory gets a colour hashed from that
  directory, so a repository keeps a recognisable colour across launches.
- A second window opened in the **same** directory would collide, so it falls
  back to a hash of the session id, stepping through the palette to skip any
  colour a live session already holds. No two windows share a tint.
- Liveness is tracked in `~/.claude/hooks/state/terminal-bg/<session-id>.json`,
  keyed to the `claude` process id. Entries left behind by a killed session are
  pruned at the next launch.
- On exit the entry is removed and OSC 111 resets the background to your
  terminal profile's own colour.

## Requirements

- **`python3`** on `PATH`. No third-party packages, no `jq`.
- **A terminal that honours OSC 11.** Verified on GNOME Terminal (VTE 0.76).
  kitty, WezTerm, Alacritty, iTerm2, Konsole and foot support it too; Apple's
  Terminal.app does not. To check yours:

  ```shell
  printf '\033]11;#2b1b3d\007'   # tint
  printf '\033]111\033\\'        # reset
  ```

- **Linux or macOS.** The terminal device is found by walking the process tree
  via `/proc` where it exists and a single `ps` snapshot otherwise. Only the
  `/proc` path has been tested; the `ps` path is written against documented
  macOS `ps` output but unverified on a Mac.

## Tuning

The palette is `BACKGROUND_COLOUR_PALETTE` at the top of
`terminal-tint/scripts/terminal-bg-tint.py`: twelve dark tints in the same
luminance band, chosen so a dark theme's foreground colours and Claude Code's
syntax highlighting stay readable. On a **light** theme they will all read as
near-black — replace them with twelve pale tints instead.

## Why a hook and not a setting

Escape sequences go straight to the terminal device (`/dev/pts/N`), not to
stdout: Claude Code captures `SessionStart` stdout and feeds it into the model's
context, so anything printed there would never reach the screen.

## License

[MIT](LICENSE)
