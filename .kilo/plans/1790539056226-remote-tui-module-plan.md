# Remote TUI Module — Plan

## Goal

Add a new TUI module under `realtimecontrol/rtr/rtr/remote/` to control the robot from a remote handheld with a small text-only screen (foot terminal). The module reads joystick input and sends commands to the core, displaying the current zone, available actions, and exit zones.

## Scope

- New package `rtr.remote` with:
  - `__init__.py` — entry point, config loading, main loop
  - `config.py` — remote-specific settings (network, screen size, theme)
  - `connection.py` — WebSocket/HTTP client to talk to core
  - `tui.py` — urwid-based terminal UI
  - `events.py` — joystick event processing
- Update `rtr/requirements.txt` to include `urwid`
- No changes to core modules
- No changes to existing remote files except adding the new files

## Decisions

### Library
**urwid** — a mature terminal UI framework. It gives us:
- Native terminal output (foot respects it)
- Simple layout building
- Keyboard handling (joystick events)
- Reasonable size constraints

### Network Protocol
**WebSocket** — matches the existing client.html path:
- Core already exposes a WebSocket server (port 8765 default)
- Commands and state are the same JSON format as the browser client
- No extra API surface to implement

### Config File
`rtr/remote/config.json` — simple JSON with defaults:
```json
{
  "core_host": "127.0.0.1",
  "core_port": 8765,
  "screen_width": 64,
  "screen_height": 4,
  "theme": "default"
}
```

### Screen Layout
Fixed-height TUI (4 lines in the current plan; extra lines reserved):
```
Line 1: Zone (large) + Mode
Line 2: Actions (list, scrollable)
Line 3: Exit zones (list, scrollable)
Line 4: Status / pad (future info)
```

## Files to Create

| Path | Purpose |
|------|---------|
| `rtr/remote/config.py` | Config dataclass, CLI loader |
| `rtr/remote/connection.py` | WebSocket client wrapper |
| `rtr/remote/events.py` | Joystick reading adapter |
| `rtr/remote/tui.py` | urwid UI implementation |
| `rtr/remote/__init__.py` | Main entry point |
| `rtr/remote/config.json.example` | Sample config |
| `rtr/requirements.txt` | Add `urwid` |

## Implementation Steps

1. Add `urwid` to `rtr/requirements.txt`
2. Create `config.py` — dataclass mirroring the JSON schema, CLI `--host`, `--port`, etc.
3. Create `connection.py` — WebSocket client using existing `ws` API; subscribe to `state` frames, send commands
4. Create `events.py` — wrap `joystick_test.py` logic: open device, read events, translate to joystick delta, expose `Joystick` class
5. Create `tui.py` — urwid layout:
   - Main loop reads `Joystick` events, updates UI
   - State read from `Connection`
   - Render zone, actions, exits
   - Handle resize (optional)
6. Create `__init__.py` — parse args, load config, instantiate components, start UI loop, handle Ctrl-C
7. Add `config.json.example` with defaults and instructions
8. Test locally against core (sim mode)
9. Test network connection (different host/port)
10. Document usage

## Validation

- Lint: `ruff check rtr/remote/`
- Type check: `mypy rtr/remote/`
- Run: `python rtr/remote/__init__.py --help`
- UI render check: run against `main.py --sim`
- Network check: `python rtr/remote/__init__.py --host 192.168.1.50 --port 8765`

## Risks / Caveats

- Foot terminal may have different color handling — stick to plain text for now
- Joystick event codes are platform-specific — only EV_KEY and EV_ABS are used, rest ignored
- Network latency — WebSocket will buffer commands; core will process them in order
- Screen size — urwid truncates output, but layout must stay within configured bounds

## Next Task

Implement `config.py` and `requirements.txt` update.
