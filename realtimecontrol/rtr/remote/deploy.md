# Deploy Runbook — RTR Remote (Trimui / R36S)

How to ship the updated remote app code to the device and verify it runs.
Kept here so a future session can deploy without re-reconning.

## Device

| Field | Value |
|-------|-------|
| Target | `root@192.168.1.33` |
| **Password** | **`tina`** |
| OS | Trimui / R36S, `Linux TinaLinux 4.9.191 ... aarch64` |
| Auth | **Password only. No SSH keys.** No `sshpass` installed, `sudo` is not non-interactive. |

> Auth constraint: the working path is **paramiko with password auth** (no keys,
> no sudo). Local SSH key auth is broken (`~/.ssh/id_rsa` sits in the GCR agent
> at `SSH_AUTH_SOCK=/run/user/1000/gcr/ssh` but the agent "refused operation").
> A throwaway key `~/.ssh/id_deploy_rtr` was generated but deliberately NOT
> installed — the user wants password auth only.

## On-device layout

| Path | What | Ships as |
|------|------|----------|
| `/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/` | Static PAK (~200MB): `python/`, `venv/`, `fonts/`, `launch.sh`, `config.json` | once (slow over wifi) |
| `/storage/rtr-remote/` | App code = the `rtr/` tree (repo `"repo"` field) | for every code update (tiny) |

The PAK bundles the interpreter + pygame (SDL2) + websockets + fonts. The app
code is shipped separately so code changes do **not** re-send the ~200MB PAK.

`config.json` `"repo"` must equal the app-code path (`/storage/rtr-remote`);
`launch.sh` reads it via `jq`.

## Build / stage (dev machine, x86_64)

```bash
# Stage ONLY the static parts + app code (no network, no python/venv download):
bash realtimecontrol/rtr/scripts/build_pak.sh
#   -> dist/RTRREMOTE.pak/   (launch.sh, config.json, fonts)
#   -> dist/repo/rtr/        (the lean app-code tree; includes rtr/remote/led.py)
```

`build_pak.sh` CODE_FILES already include `rtr/remote/led.py` (added when
`ledtest.py` started importing it). The 15 staged files land in `dist/repo`.

## Ship app code to the device

The script's `--ship-code` flag uses `ssh`/`scp` (needs a key or `sshpass`),
which is NOT available here. The method that actually works is **paramiko,
password auth, tar-over-ssh** — `ship_code.py`.

```bash
# 1. Re-stage the code (see Build/stage above) so dist/repo is fresh.
bash realtimecontrol/rtr/scripts/build_pak.sh

# 2. Ship it (password auth, streams a tar of dist/repo/rtr into /storage/rtr-remote):
python3 /tmp/kilo/ship_code.py
```

`ship_code.py` does, in order:
1. Build a tar of `realtimecontrol/rtr/dist/repo/rtr` (arcname `rtr`) in memory.
2. Connect to `root@192.168.1.33` with password `tina` (`allow_agent=False`,
   `look_for_keys=False` → forces password).
3. Verify `/storage/rtr-remote` exists/is writable (`mkdir -p && test -d`).
4. Stream the tar over an ssh channel; device runs `tar -C /storage/rtr-remote -xf -`.
5. `ls` the on-device `rtr/remote/` to confirm `led.py` + the updated set landed.
6. Run the on-device selftest (see Verify).

> `ship_code.py` is cwd-relative: run it from the repo root
> (`realtimecontrol/rtr/dist/repo` is resolved from cwd). Keep cwd = repo root.

### Rebuild `ship_code.py` if `/tmp` was wiped

`/tmp/kilo/ship_code.py` is ephemeral. If it's gone, recreate it: a paramiko
client that (a) tars `dist/repo/rtr` with arcname `rtr`, (b) connects
`root@192.168.1.33` pw `tina`, (c) `tar -C /storage/rtr-remote -xf -` over a
channel, (d) `ls rtr/remote/`, (e) runs the selftest. The earlier
`deploy_recon.py` has the connect/recon pattern if you need it.

## Verify on the device

```bash
# Headless selftest via the PAK's bundled python (no core, dummy driver):
ssh root@192.168.1.33 "/mnt/SDCARD/Tools/tg5040/RTRREMOTE.pak/launch.sh --selftest"
#   (paramiko equivalent: run that command via an exec_command in ship_code.py)
```

`launch.sh --selftest` runs `mainmenu.py --selftest` (the menu entrypoint), which
sets `SDL_VIDEODRIVER=dummy` and renders headless. `sdlapp.py --selftest` and
`ledtest.py --selftest` have their own headless selftest paths.

### Known current status (2026-10-21)

The selftest currently FAILS with rc=1:

```
MENU SELFTEST FAIL: dummy not available
```

The SDL **dummy** video driver is unavailable on the device. `launch.sh`
force-preloads the SYSTEM SDL2 (`/usr/trimui/lib/libSDL2-2.0.so.0`, built with
the framebuffer driver, not the offscreen/dummy one) to override the bundled
pygame SDL2 (RPATH wins over `LD_LIBRARY_PATH`). That system build may not ship
the `dummy` driver → the headless `--selftest` can't init display.

Next step when resuming: diagnose why `dummy` is unavailable (inspect
`launch.sh` SDL/driver setup + whether the system SDL2 exposes `dummy`), get
`--selftest` passing, then confirm the new pages render against live core state.

## Pages (9, in order)

NAV, CAMERA, BRAIN, HUNT, CADENCE, TRIGGERS, ARM, LED, SETUP

New TUI features shipped in this round:
- **TRIGGERS** page — event log.
- **LED** page — 22-LED sysfs control (`led.py` pure module; `ledtest.py` app).
- **Text ports** of `editor.html` (safety / autonomy / MIDI / target / hw-limit bars).
- **ARM** page.

## Key files

| File | Role |
|------|------|
| `realtimecontrol/rtr/scripts/build_pak.sh` | Build PAK + stage `dist/repo`; `--ship-code` ships code (ssh/scp). CODE_FILES include `rtr/remote/led.py`. |
| `/tmp/kilo/ship_code.py` | Paramiko password-auth deploy (tar-over-ssh) + selftest. The method that works. |
| `/tmp/kilo/deploy_recon.py` | Paramiko recon + (unused) key-install pattern. |
| `realtimecontrol/rtr/pak/RTRREMOTE.pak/launch.sh` | Device entrypoint; bundles python/venv, forces system SDL2, runs `mainmenu.py`. |
| `realtimecontrol/rtr/rtr/remote/led.py` | New pure 22-LED sysfs module (shipped). |
| `realtimecontrol/rtr/rtr/remote/view.py` | Shared view layer; 9-page name-based dispatch + `RemoteState`. |
| `realtimecontrol/rtr/rtr/remote/tui.py` | urwid renderer; LED sysfs lifecycle. |
| `realtimecontrol/rtr/rtr/remote/ledtest.py` | SDL LED app; imports from `.led`. |
| `realtimecontrol/rtr/rtr/remote/sdlapp.py` | SDL renderer + selftest (the failing `--selftest` path). |
| `realtimecontrol/rtr/rtr/remote/config.json` | Device config; `"repo": "/storage/rtr-remote"`. |
