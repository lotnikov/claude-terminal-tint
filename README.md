# terminal-tint

A Claude Code plugin that gives every concurrent Claude window its own terminal
background colour, so you can tell at a glance which window is which task. On the
Cinnamon desktop the same colour also appears as a badge on the window's button
in the panel, so you can find the right window before you switch to it.

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

## Panel badge (Cinnamon)

On Cinnamon under X11 each terminal window's button in the panel gets a dot in
the session's colour — the same hue as the background, lifted in lightness so it
stays visible on a dark panel.

- **One badge per window.** Several tabs share one panel button, so the first
  session in a window owns the badge and later tabs leave it alone. When the
  owner exits, the badge passes to the longest-running session still in that
  window; when the last one exits, the normal icon comes back.
- **Finding the window.** GNOME Terminal runs all windows in one process and
  exports no `WINDOWID`, so the hook briefly gives the tab a unique title, finds
  the X window carrying it, and restores the previous title with the xterm title
  stack (`CSI 22;0t` / `CSI 23;0t`). Only the active tab sets the window title,
  so a session started in a background tab gets no badge (and waits 1.5 s
  looking for it).
- **Publishing the icon.** The badge is a cached PNG in
  `~/.claude/hooks/state/terminal-badges/`, set through the XApp
  `_NET_WM_XAPP_ICON_NAME` window property, which Cinnamon prefers over the
  application icon. The panel only re-reads icons when `_NET_WM_ICON` changes, so
  the hook appends zero items to it: the icon data is untouched but the X server
  still sends the change notification.
- **Needs** the grouped window list with *Group windows by application* turned
  off (one button per window), `python3-xlib`, ImageMagick (`convert` or
  `magick`) and PyGObject to look up the terminal icon. Without any of them, or
  on Wayland or macOS, the badge is skipped silently and only the background is
  tinted.
- A tab dragged into another window leaves its badge on the old window.

## Requirements

- **`python3`** on `PATH`. The background tint needs no third-party packages
  and no `jq`; the optional panel badge has its own needs, listed above.
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

The badge colour is derived from the background by `BADGE_LIGHTNESS` and
`BADGE_MINIMUM_SATURATION` in the same file.

## Why a hook and not a setting

Escape sequences go straight to the terminal device (`/dev/pts/N`), not to
stdout: Claude Code captures `SessionStart` stdout and feeds it into the model's
context, so anything printed there would never reach the screen.

## License

[MIT](LICENSE)
