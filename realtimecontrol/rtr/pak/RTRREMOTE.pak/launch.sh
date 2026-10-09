#!/bin/sh
# RTRREMOTE PAK entrypoint (NextUI / Trimui).
#
# Runs the SDL2 remote app with the PAK's bundled Python + libs. The app code
# lives in the FTI_kuka repo at a fixed device path ($REPO, default
# /storage/rtr-remote); the PAK only bundles the interpreter, pygame (SDL2) and
# websockets, plus fonts and a config.json.
#
#   launch.sh            -> live app (joystick + keyboard), L1+R1 quits
#   launch.sh --selftest -> headless self-test (dummy driver), prints PASS/FAIL
#
# NextUI env (USERDATA_PATH, LOGS_PATH, DEVICE) is used when present; sane
# defaults let `launch.sh --selftest` run over ssh.
set -eu

PakDir=$(dirname "$0")

# Fixed repo path on the device; read from config.json when jq is available.
REPO=$(jq -r .repo "$PakDir/config.json" 2>/dev/null || echo /storage/rtr-remote)
[ -n "${REPO:-}" ] || REPO=/storage/rtr-remote

# venv site-packages (pygame + websockets) on PYTHONPATH; the SDL2 libs that
# pygame bundles live in a top-level pygame.libs/ dir (hashed .so names).
VENV_SP=$(ls -d "$PakDir"/venv/lib/python3.*/site-packages 2>/dev/null | head -n1)
if [ -z "$VENV_SP" ]; then
    echo "!! no venv site-packages under $PakDir/venv"; exit 1
fi
export PYTHONPATH="$REPO:$VENV_SP${PYTHONPATH:+:$PYTHONPATH}"
# System SDL2 first: the PAK's *bundled* SDL2 is a desktop build with only the
# offscreen driver (it never binds /dev/fb0 -> black panel). The TrimUI system
# SDL2 (/usr/trimui/lib) is built with the framebuffer driver. Put it ahead of
# the bundled pygame.libs so the dynamic linker resolves libSDL2 to the system
# one; the bundled libs still back up anything the system lacks. Matches the
# working Dronage Terminal PAK (system SDL2).
export LD_LIBRARY_PATH="/usr/trimui/lib:/usr/lib:$VENV_SP/pygame.libs:$VENV_SP/pygame/.libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# Force the SYSTEM SDL2 (which has the framebuffer driver) over the bundled
# desktop SDL2. The bundled pygame .so carries RPATH -> pygame.libs, and RPATH
# is searched BEFORE LD_LIBRARY_PATH, so a plain LD_LIBRARY_PATH reorder cannot
# win. LD_PRELOAD is the only knob that overrides RPATH symbol resolution.
SYS_SDL2=$(ls /usr/trimui/lib/libSDL2-2.0.so* /usr/lib/libSDL2-2.0.so* 2>/dev/null | head -n1)
if [ -n "$SYS_SDL2" ]; then
    export LD_PRELOAD="$SYS_SDL2${LD_PRELOAD:+:$LD_PRELOAD}"
    echo "RTR: LD_PRELOAD system SDL2: $SYS_SDL2"
else
    echo "RTR: WARN no system libSDL2-2.0.so found; bundled (offscreen) SDL2 will be used"
fi
# UTF-8 so the font can render any character the core sends.
export LANG=C.UTF-8

# NextUI logging: when LOGS_PATH is set, send the app output there.
if [ -n "${LOGS_PATH:-}" ] && [ -d "$LOGS_PATH" ]; then
    exec > "$LOGS_PATH/RTRREMOTE.txt" 2>&1
fi

# The PAK's bundled interpreter. `python3` falls back to the python dir.
PY="$PakDir/python/bin/python3"
[ -x "$PY" ] || PY=python3

# Keep the screen awake while the app runs (TrimUI convention). The app is run
# in the foreground (not exec'd) so the trap can clean up on exit.
echo "1" > /tmp/stay_awake 2>/dev/null || true
cleanup() { rm -f /tmp/stay_awake 2>/dev/null || true; }
trap cleanup EXIT INT TERM

"$PY" "$REPO/rtr/remote/sdlapp.py" --config "$PakDir/config.json" "$@"
