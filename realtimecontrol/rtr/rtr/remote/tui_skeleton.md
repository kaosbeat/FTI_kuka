# TUI Skeleton: how this app is built, deployed, and how to make a new one

This document explains the architecture of the RTR remote TUI (desktop urwid TUI
+ SDL2 device app for the Trimui/NextUI handheld) and the concrete steps to
create a skeleton for a new app like this: the code-update framework, the key
mapping, PAK launching, font configuration, and the socket connection.

Everything below is grounded in the actual modules in this directory:

| File | Role |
|---|---|
| `view.py` | Pure view layer: state machine, row building, all shared constants (pages, app keys, default input maps, calibration sequence). No framework imports. |
| `config.py` | `Config` dataclass: connection, joystick, screen/font/theme, PAK repo path. JSON round-trip + CLI flags. |
| `events.py` | Raw `/dev/input` joystick reader (`Joystick`), `struct` event unpacking. |
| `connection.py` | WebSocket + HTTP client to the core, auto-reconnect, callback queue. |
| `tui.py` | Desktop renderer: urwid, `Theme` palette, MainLoop + worker thread. |
| `sdlapp.py` | Device renderer: pygame/SDL2, `Renderer`, `InputController`, `SdlRemote`, calibration wizard, selftest, diagnostics. |
| `../pak/RTRREMOTE.pak/` | The PAK: `launch.sh`, `config.json`, `fonts/`, `icon.png` (+ `python/`, `venv/` after `--full`). |
| `../../scripts/build_pak.sh` | Cross-build + deploy script (dev machine, x86_64). |

---

## 1. The big picture: one view layer, two renderers

The design rule that makes the skeleton reusable:

```
        ┌──────────────────────────────────────────┐
        │  view.py   (pure: RemoteState, rows)    │
        └──────────────┬────────────────┬──────────┘
                       │                │
             ┌─────────▼──────┐  ┌──────▼───────────┐
             │  tui.py        │  │  sdlapp.py       │
             │  urwid (desktop)│  │  pygame/SDL2     │
             │  TerminalTheme  │  │  (Trimui panel)  │
             └────────────────┘  └──────────────────┘
                       │                │
                       └───────┬────────┘
              ┌────────────────┴────────────────┐
              │  connection.py  (WebSocket +    │
              │  HTTP, worker thread, queue)    │
              └────────────────────────────────┘
              ┌─────────────────────────────────┐
              │  events.py  (raw /dev/input)    │
              └─────────────────────────────────┘
```

- **`view.py` is framework-free.** It turns (zone/action table + live state +
  page/cursor) into a list of *rows* and a list of *selectable items*. A row is
  a dict with a `style` and either `text` or `pairs`:

  ```python
  {"text": "  ▸ zone A", "style": "item_sel"}
  {"pairs": [("ZONE", "a"), ("MODE", "idle")], "style": "state"}
  {"style": "divider"}
  ```

  Style keys are plain strings (`bar`, `state`, `item`, `item_sel`, `divider`,
  `label`, `help`, `feral`, …). Each renderer maps them to its own palette
  (`Theme` in `tui.py`, `DEFAULT_COLORS` in `sdlapp.py`). This is what lets the
  same state machine drive a terminal and a framebuffer.

- **The renderers are thin.** They own I/O (terminal or display), fonts, input
  dispatch, and the main loop. All decisions about *what* to show and *what a
  selection does* live in `view.RemoteState` (`build_rows`, `activate`,
  `move_cursor`, `set_page`, `apply_zones`, `apply_state`).

- **Input is unified to a small app-key set** before it reaches the state
  machine (see §3): `up`, `down`, `left`, `right`, `enter`, `q`, `escape`, `h`
  (`view.APP_KEYS`), plus the synthetic `page_left` / `page_right`.

To create a new app skeleton: copy this layout, keep `view.py` pure, and write
two thin renderers (or one, if you only target one platform).

---

## 2. Framework for updating the code (PAK vs. code split)

The deployment is split into two artifacts with very different sizes and
lifetimes:

| Artifact | Size | Changes | Ship cost |
|---|---|---|---|
| **The PAK** (`python/` + `venv/` + `fonts/` + `config.json` + `launch.sh` + `icon.png`) | ~200 MB | almost never | slow (wifi) |
| **The app code** (the `rtr/` tree, a few hundred KB) | tiny | often | fast |

This split is the core of the update framework: **code updates never re-send
the PAK.**

### 2.1 Build script: `scripts/build_pak.sh`

Runs on the dev machine (x86_64), cross-builds for the device:

1. **Static parts** (always): copy `launch.sh`, `config.json`, `icon.png`
   from `pak/RTRREMOTE.pak/` into `dist/RTRREMOTE.pak/` (regenerating the icon
   via `scripts/make_icon.py` if missing).
2. **Font**: copy `DejaVuSansMono.ttf` into `dist/.../fonts/` (from the PAK
   dir or `/usr/share/fonts`).
3. **`python/`** (only with `--full`): download the pinned
   python-build-standalone aarch64 tarball
   (`cpython-3.11.17+20261003-aarch64-unknown-linux-gnu-install_only.tar.gz`)
   and extract its top-level `python/` dir.
4. **`venv/`** (only with `--full`): `pip download --only-binary=:all:
   --platform manylinux2014_aarch64 --python-version 311 pygame websockets`
   and unzip each wheel into `venv/lib/python3.11/site-packages/`. pygame
   bundles SDL2 under `pygame.libs/` (hashed `.so` names); the script verifies
   it landed.
5. **App code** (always): copy the exact closure of `rtr.remote.sdlapp`
   (`CODE_FILES` list: `rtr/__init__.py`, `rtr/flow.py`, `rtr/remote/*`
   including `sdlapp.py`, `view.py`, `config.py`, `connection.py`, `events.py`,
   `tui.py`, `clockfix.py`, plus the two shipped fonts) into `dist/repo/`.
6. **`--ship-code`**: ship ONLY the app code to the device (no PAK rebuild):

   ```bash
   scripts/build_pak.sh --ship-code
   ```

   It streams the tree over ssh via tar (no rsync on the device, avoids the
   OpenSSH 9.x `scp -r` "path canonicalization failed" error):

   ```bash
   tar -C dist/repo -cf - rtr | ssh root@192.168.1.33 "tar -C /storage/rtr-remote -xf -"
   ```

   The destination is `REPO_PATH` (default `/storage/rtr-remote`), which must
   match the `"repo"` field in the PAK's `config.json`.

### 2.2 The two-artifact lifecycle

- **First deploy** (once, slow): `build_pak.sh --full`, then
  `scp -r dist/RTRREMOTE.pak root@192.168.1.33:/mnt/SDCARD/Tools/tg5040/`.
- **Every code update** (fast): edit the repo, run `build_pak.sh --ship-code`.
  `launch.sh` picks up the new code immediately because it runs from `$REPO`.

### 2.3 On-device verification (no git, no rsync)

The PAK ships its own test entry points so a code update can be verified on the
device before trusting it:

```bash
ssh root@192.168.1.33 "/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --selftest"
ssh root@192.168.1.33 "/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --diag"
ssh root@192.168.1.33 "/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --ws-test"
ssh root@192.168.1.33 "/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --joy-diag"
```

- `--selftest`: headless render of all pages with mock state (dummy SDL
  driver). NOTE: on this device the system SDL2 has no `dummy` driver, so the
  headless selftest cannot run on-device; it passes locally (32/32). Use
  `--diag` on-device instead.
- `--diag`: init the real display, print the framebuffer diagnostic, exit.
  Fast, isolated probe of the SDL video driver + physical fb state.
- `--ws-test`: raw TCP connect → WS handshake → hello → first state frame, each
  step timed and named; prints `WS-TEST PASS/FAIL` with the failing step.
- `--joy-diag`: read the joystick device and print every EV_KEY/EV_ABS event
  until Ctrl-C; lists distinct button codes so `joy_map`/`dpad_map` can be
  corrected.

### 2.4 Skeleton checklist for a new app

1. Create `pak/<APP>.pak/` with `launch.sh`, `config.json`, `fonts/`, `icon.png`.
2. Add a `CODE_FILES` list to a `build_pak.sh` (reuse this one, change the list).
3. Pick a `REPO_PATH` on the device and mirror it in `config.json "repo"`.
4. Add `--selftest`, `--diag`, `--ws-test`, `--joy-diag` entry points to the app
   module (see `sdlapp.main` for the pattern).
5. First deploy the PAK once; thereafter ship only code with `--ship-code`.

---

## 3. Mapping the keys (joystick + keyboard)

Input is read **raw from `/dev/input`** (the `events.Joystick` reader) and
unified to app keys. pygame's joystick API is deliberately disabled
(`SDL_JOYSTICK=disabled` in `sdlapp.run`), because SDL would scan + grab the
same `/dev/input/eventN` device and send EOF to the raw reader. The keyboard
comes from SDL's video subsystem and is mapped directly.

### 3.1 The two input families

- **EV_KEY (type 1)** — digital buttons: the face buttons (A/B/X), the
  single-button quit (home), and the L1/R1 shoulders.
- **EV_ABS (type 3)** — the d-pad, an **analog axis**. On the Trimui:
  - axis 17 (TRACKBALL): negative = UP, positive = DOWN
  - axis 16 (WHEEL): negative = LEFT, positive = RIGHT

  Only the **sign** matters (center = 0). The raw ABS value is unpacked as an
  unsigned 32-bit int, so a negative axis comes back as a large unsigned number
  (`4294967295` = -1). `_abs_sign()` recovers the sign:

  ```python
  def _abs_sign(value):
      signed = value - 0x100000000 if value >= 0x80000000 else value
      return -1 if signed < 0 else (1 if signed > 0 else 0)
  ```

  D-pad mapping is encoded as `{app_key: [axis_code, sign]}`:

  ```python
  DEFAULT_DPAD_MAP = {
      "up": [17, -1], "down": [17, 1],
      "left": [16, -1], "right": [16, 1],
  }
  ```

  A **press edge** (axis leaving center in a mapped direction) emits the
  direction; holding it does not repeat; returning to center emits nothing
  (`InputController._on_abs`).

### 3.2 The app-key unification

`view.APP_KEYS = ("up","down","left","right","enter","q","escape","h")`.
Both renderers reduce all input to this set plus `page_left`/`page_right`
before calling `RemoteState`. The keyboard map is `KEY_MAP` in `sdlapp.py`
(SDL `KEYDOWN` → app key). The joystick map is `InputController.on_joystick_event`:

- `value == 1` (press edge) on a `quit_codes` code → `("quit",)` (home = 172).
- L1 (310) / R1 (311) → `page_left` / `page_right` **before** any `joy_map`
  lookup. L1/R1 are reserved for **global** page navigation and are never
  mappable — this is the reliable way out of any page (including SETUP).
- Any other EV_KEY press edge → `joy_map.get(code)` → app key.

Default `joy_map` (`view.DEFAULT_JOY_MAP`):

```python
{15: "up", 16: "down", 17: "left", 18: "right",
 29: "enter", 30: "q", 8: "h",
 310: "left", 311: "right"}
```

(For a different gamepad, override `joy_map` in `config.json`.)

### 3.3 The calibration wizard (first launch)

Because a new device may have different axis/codes, the app ships with
`calibrated: false` and runs a **calibration wizard** on first launch (the SETUP
"calibrate" row re-runs it on demand). The wizard is `view.CALIB_SEQUENCE`:

```python
CALIB_SEQUENCE = (
    ("up", "abs"), ("down", "abs"), ("left", "abs"), ("right", "abs"),
    ("enter", "key"), ("q", "key"), ("h", "key"),
)
```

Each step prompts the user ("press UP now", "press A (enter)", …) and captures
the next matching press edge:

- `kind == "abs"`: capture the d-pad `(axis, sign)` on a press edge
  (`_set_dpad_map`).
- `kind == "key"`: capture the face-button EV_KEY code on a press edge
  (`_set_joy_map`, replacing any prior non-L1/R1 mapping).

L1/R1 during a capture **aborts** the capture (and still pages as normal). On
completing all steps, the wizard sets `calibrated = true` and persists the map
to `config.json` (`_persist_settings`). The mapper rows on the SETUP page also
offer per-button re-capture (`_start_capture_for([(ak, kind)])`).

### 3.4 Discovering the real codes

Run `--joy-diag` on the device: it prints every EV_KEY/EV_ABS event until
Ctrl-C and lists the distinct KEY codes it saw. Map those codes into
`config.json` (`joy_map` for digital buttons, `dpad_map` for the d-pad axis +
sign). This is how you correct a wrong map after a hardware change.

---

## 4. Launching the PAK (`launch.sh`)

The PAK entrypoint (`pak/RTRREMOTE.pak/launch.sh`) is a POSIX shell script that
NextUI runs. Its job: point the bundled Python at the right libs and the right
SDL2, then run the app.

```sh
PakDir=$(dirname "$0")
REPO=$(jq -r .repo "$PakDir/config.json" 2>/dev/null || echo /storage/rtr-remote)
VENV_SP=$(ls -d "$PakDir"/venv/lib/python3.*/site-packages | head -n1)
export PYTHONPATH="$REPO:$VENV_SP${PYTHONPATH:+:$PYTHONPATH}"
# System SDL2 first (framebuffer driver); bundled pygame.libs as backup.
export LD_LIBRARY_PATH="/usr/trimui/lib:/usr/lib:$VENV_SP/pygame.libs:$VENV_SP/pygame/.libs"
# Force the SYSTEM SDL2 over the bundled desktop SDL2 (RPATH wins over
# LD_LIBRARY_PATH, so LD_PRELOAD is the only knob that overrides RPATH).
SYS_SDL2=$(ls /usr/trimui/lib/libSDL2-2.0.so* /usr/lib/libSDL2-2.0.so* | head -n1)
[ -n "$SYS_SDL2" ] && export LD_PRELOAD="$SYS_SDL2"
export LANG=C.UTF-8
[ -n "${LOGS_PATH:-}" ] && exec > "$LOGS_PATH/RTRREMOTE.txt" 2>&1
PY="$PakDir/python/bin/python3"
echo "1" > /tmp/stay_awake 2>/dev/null || true   # keep the panel awake
trap 'rm -f /tmp/stay_awake' EXIT INT TERM
"$PY" "$REPO/rtr/remote/sdlapp.py" --config "$PakDir/config.json" "$@"
```

Key points:

- **`PYTHONPATH`** = `$REPO` (the app code, shipped separately) + the venv
  site-packages (pygame + websockets).
- **The SDL2 trap**: the PAK's *bundled* SDL2 (under `pygame.libs/`) is a
  desktop build with only the `offscreen` driver — it never binds `/dev/fb0`,
  so the panel stays black. The TrimUI *system* SDL2 (`/usr/trimui/lib`) is
  built with the framebuffer driver. Because the bundled pygame `.so` carries
  an RPATH to `pygame.libs` (and RPATH is searched **before** `LD_LIBRARY_PATH`),
  a plain `LD_LIBRARY_PATH` reorder cannot win — **`LD_PRELOAD` of the system
  `libSDL2-2.0.so` is the only knob** that overrides RPATH symbol resolution.
- **`LANG=C.UTF-8`** so the font can render any character the core sends.
- **`stay_awake`** keeps the panel on (TrimUI convention); the `trap` removes it
  on exit. The app runs in the foreground (not `exec`) so the trap fires.
- **`$@`** forwards CLI flags (`--selftest`, `--diag`, `--ws-test`,
  `--joy-diag`) through to the app.

### 4.5 The video-driver probe (inside the app)

Even with the right SDL2, SDL's auto-detect on this device can select the
`offscreen` driver. `SdlRemote._video_driver()` probes the candidate
framebuffer drivers in order — `fbcm`, `kmsdrm`, `kms` — and returns the first
whose `set_mode` succeeds with a real (non-offscreen/dummy) driver. An explicit
`SDL_VIDEODRIVER` env override always wins (manual debugging). `--diag` prints
the active driver, the loaded `libSDL2` path (`/proc/self/maps`), the screen
size, and the `/sys/class/graphics/fb0` state, so a black screen is diagnosed in
one command.

---

## 5. Setting the font

The font is configured in `config.json` and resolved/scaled by `sdlapp.py`:

- **`font`** (path): resolved by `_resolve_font()` in this order:
  1. the literal path (absolute, or CWD-relative),
  2. relative to the **config file's directory** (the PAK dir — so
     `fonts/DejaVuSansMono.ttf` works regardless of CWD),
  3. relative to the **module's directory** (`rtr/remote/`, where `--ship-code`
     puts fonts shipped with the code, e.g. `PressStart2P.ttf`).
  If none is found, the pygame default font is used.
- **`font_size`** (px, default 16): the base size, sized for the **design
  canvas**.
- **`design_height`** (px, default 240): the reference height the `font_size`
  is designed for. On a taller render height the font is scaled
  proportionally so the same number of rows fills the panel:

  ```python
  def _font_size_for(w, h):
      base = config.font_size            # e.g. 16
      design_h = config.design_height    # e.g. 240
      return max(1, int(round(base * (h / design_h))))
  ```

  The reference is `design_height` (NOT the render `screen_height`), so a config
  that sets `screen_height` to the native panel height still scales up
  correctly. The render size is the larger of the config canvas
  (`width`/`screen_height`, default 320×240) and the panel's native size
  (e.g. the Trimui's 1024×768), so on the device the font scales up from 16 to
  ~52px automatically.
- **`theme`** (style key → hex): overrides the `DEFAULT_COLORS` table, merged
  by `_colors()`.

The `Renderer` measures the monospace font's character cell (`char_w`,
`char_h`) and computes `cols`/`rows`; `build_rows(cols, …)` in `view.py` then
wraps text to those columns. So the font, the render size, and the row layout
are all derived from the same metrics — change the font and the layout follows.

---

## 6. Connecting to the socket

The connection is owned by `connection.py` and runs on a **worker thread** so
the renderer's main loop (urwid or pygame) never blocks.

- **WebSocket** to the core (`core_host:core_port`, default `8765`): the live
  link. The core broadcasts state every tick (~20 Hz); the remote sends
  commands over the same socket. Auto-reconnect is built in.
- **HTTP** to the core's zone table (`http_host:http_port`, default `8766`):
  if the WebSocket disconnects, the remote polls `/api/zones` every
  `http_poll` seconds (default 30) to keep the zone/action table fresh.
- **Callback queue**: the worker thread pushes state/zone updates onto a queue
  that the main loop drains each frame; commands are sent back via
  `call_soon_threadsafe` (see `SdlRemote._send`).

The app's `--ws-test` is the isolated link probe (no UI, no joystick): it
performs, in order, (1) a raw TCP connect, (2) the WS handshake, (3) a hello
send, (4) a first state frame read — each timed and named, so the exact hanging
step is exposed:

```
WS-TEST [1] TCP connect OK in 0.012s
WS-TEST [2] WS handshake OK in 0.014s
WS-TEST [3] hello sent in 0.001s
WS-TEST [4] first frame type=state in 0.002s
WS-TEST PASS: full link up
```

A `WS-TEST FAIL [n] <step>` line names the failing step as the reason the live
link is down.

### 6.1 The threading model (mirrored by both renderers)

- `RemoteState` (in `view.py`) holds all state and is mutated on the main
  thread only.
- The worker thread runs the asyncio connection loop and pushes updates via the
  queue.
- The main loop (urwid `MainLoop` or the pygame `while not quit` loop) drains
  the queue, applies updates to `RemoteState`, renders, and pumps input.
- Commands from a selection are sent back to the worker thread with
  `call_soon_threadsafe(conn.send_command, cmd)`.

---

## 7. Putting it all together (skeleton recipe)

1. **View layer** — write a pure `view.py` (state machine + row building +
   shared constants: pages, app keys, default input maps, calibration
   sequence). No framework imports.
2. **Config** — a `Config` dataclass with connection, input, screen/font/theme,
   and PAK repo fields. `from_json` filters to known fields (tolerant of
   legacy/non-field keys); `to_json` dumps `self.__dict__`; `from_args` builds
   from a `--config` file + explicit CLI flags.
3. **Renderers** — one thin renderer per platform, mapping the view's style
   keys to its palette and unifying input to `APP_KEYS`. Disable the platform's
   own joystick API if it would grab the raw input device.
4. **Input** — read the joystick raw from `/dev/input`; map EV_KEY (digital) +
   EV_ABS (d-pad axis sign); reserve L1/R1 for global page nav; make home the
   only quit; add a first-launch calibration wizard + per-button re-capture.
5. **PAK** — a `launch.sh` (PYTHONPATH + LD_PRELOAD for the system SDL2 +
   stay_awake + log redirect + `$@` passthrough), a `config.json`, `fonts/`,
   `icon.png`.
6. **Deploy** — a `build_pak.sh` that cross-builds the PAK (`--full`) and ships
   only the code (`--ship-code`, tar-over-ssh) to a fixed `REPO_PATH`.
7. **Diagnostics** — `--selftest` (headless), `--diag` (framebuffer),
   `--ws-test` (link), `--joy-diag` (input codes) as isolated entry points.

That is the complete skeleton: a pure view layer, two thin renderers, a
tolerant config, a raw-input mapping with a calibration wizard, a PAK launcher
that forces the system SDL2, and a two-artifact deploy split so code updates
are fast and the PAK is sent once.
