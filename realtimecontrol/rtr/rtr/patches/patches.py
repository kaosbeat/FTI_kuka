"""Patches adapter (hydra screen code).

The tool's screen (and the fullscreen render page) shows a hydra patch. The
patches are **data-driven**: they live in ``patches.json`` (edited in
``editor.html``, served/saved over ``/api/patches``, hot-reloaded). The built-in
:data:`DEFAULT_HYDRA_CODE` remains the fallback when the file is missing or
corrupt.

A patch is a **hydra-synth JS code string**. The table maps the core's state to
code:

- ``default`` — the code used as the action-slot fallback,
- ``zones``   — zone name -> code (zone layer, renders to ``o0``),
- ``modes``   — behavior name -> code (behavior layer, renders to ``o3``),
- ``actions`` — two-level map ``zone name -> action name -> code`` (action layer, renders to ``o1``).

The core concatenates the three layers in order — **zone → action/default →
behavior** — and pushes the result in every state frame. Each layer's output
slot (``o0``, ``o1``, ``o3``) is declared by the patch code itself (via
``.out(oN)``); the core joins the raw code strings verbatim. The browser pages
fetch the whole table once and match locally on every state frame (no
per-tick traffic); a ``PATCHES_CHANGED`` event tells them to re-fetch after a
hot-reload.
"""

import json
from typing import Any

from ..core.bus import StateBus
from ..core.commands import Event


# The hydra patch used when nothing else matches (mirrors the browser-side
# fallbacks in rtr3d.js / patches.js).
DEFAULT_HYDRA_CODE = "osc(4, 0.1, 1.2).out()"


def builtin_patches_data() -> dict:
    """Build a patches table from the built-in :data:`DEFAULT_HYDRA_CODE`.

    This is the fallback shape of ``patches.json`` (see :func:`validate_patches_data`).
    """
    return {"default": DEFAULT_HYDRA_CODE, "zones": {}, "modes": {}, "actions": {}}


# ---------------------------------------------------------------------------
# Loading + validation of the on-disk data file (``patches.json``).
# ---------------------------------------------------------------------------

def _validate_table(table: Any, where: str) -> None:
    """A patches table maps a name to a non-empty hydra code string."""
    if not isinstance(table, dict):
        raise ValueError(f"'{where}' must be an object")
    for name, code in table.items():
        if not isinstance(code, str) or not code:
            raise ValueError(f"{where}[{name!r}]: patch must be a non-empty string")


def validate_patches_data(data: Any) -> dict:
    """Validate a full patches table. Returns ``data`` or raises :class:`ValueError`.

    Shape: ``{default?, zones?, modes?, actions?}`` where ``default`` is a hydra
    code string, ``zones``/``modes`` map a name to a hydra code string, and
    ``actions`` is a two-level map ``zone -> action -> hydra code string``.
    """
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    default = data.get("default")
    if default is not None and not isinstance(default, str):
        raise ValueError("'default' must be a string (may be empty)")
    for key in ("zones", "modes"):
        v = data.get(key, {})
        if not isinstance(v, dict):
            raise ValueError(f"'{key}' must be an object")
        _validate_table(v, key)
    actions = data.get("actions", {})
    if not isinstance(actions, dict):
        raise ValueError("'actions' must be an object")
    for zone, acts in actions.items():
        if not isinstance(acts, dict):
            raise ValueError(f"actions[{zone!r}]: must be an object")
        _validate_table(acts, f"actions[{zone!r}]")
    return data


def load_patches_data(path: str) -> dict:
    """Load and validate the on-disk patches file. Raises ``OSError``/``ValueError``."""
    with open(path, "r", encoding="utf-8") as f:
        return validate_patches_data(json.load(f))


def match_patch(data: dict, zone=None, mode=None, action=None) -> str:
    """The hydra code for a state (browser-local fallback): action > mode > zone > default.

    Used by the browser pages that fetch the table and match locally (when the
    core's pushed ``patch`` is unavailable). The core's main path uses
    :meth:`Patches.code_for` which concatenates zone + action/default + behavior.

    The actions table is two-level (``zone -> action -> code``); a missing zone
    (or action) falls through to the next level. An empty table returns
    :data:`DEFAULT_HYDRA_CODE`.
    """
    data = data or {}
    if zone and action:
        code = (data.get("actions") or {}).get(zone, {}).get(action)
        if isinstance(code, str) and code:
            return code
    if mode:
        code = (data.get("modes") or {}).get(mode)
        if isinstance(code, str) and code:
            return code
    if zone:
        code = (data.get("zones") or {}).get(zone)
        if isinstance(code, str) and code:
            return code
    default = data.get("default")
    if isinstance(default, str) and default:
        return default
    return DEFAULT_HYDRA_CODE


def inject_pose_globals(code: str, pose_index, pose_imgs) -> str:
    """Prepend the pose globals (``pose`` + ``poseindex``) to a resolved hydra patch.

    The "pose" level is the action patch with these globals injected at the top:
    ``pose`` is the current pose index (clamped to the image list) and ``poseindex``
    is the array of per-pose reference-image URLs (``null`` for a pose with no image).
    A patch that references ``poseindex[pose]`` then resolves to the current pose's
    image. Without a pose context (no index, or no images for the action) the code is
    returned unchanged, so mode / zone / default patches are untouched.
    """
    if pose_index is None or not pose_imgs:
        return code
    try:
        n = len(pose_imgs)
        idx = max(0, min(int(pose_index), n - 1))
    except (TypeError, ValueError):
        return code
    if n <= 0:
        return code
    urls = ['"/assets/imgs/' + im + '"' if im else 'null' for im in pose_imgs]
    header = ('// Pose reference images — pose = current pose index, poseindex[i] = image, s0 only\n'
              f'var pose = {idx}\n'
              f'var poseindex = [{", ".join(urls)}]\n\n')
    return header + code


# ---------------------------------------------------------------------------
# The adapter.
# ---------------------------------------------------------------------------

class Patches:
    """Holds the hydra patch table; hot-reloaded from disk on request.

    The table is loaded from a callable ``patches_loader`` (which returns a
    validated patches table). :meth:`reload` re-reads it and publishes
    :data:`Event.PATCHES_CHANGED` so the display can tell the pages to re-fetch.
    """

    def __init__(self, bus: StateBus, patches_loader, poses_provider=None):
        self.bus = bus
        self._patches_loader = patches_loader
        # A callable that returns the zones table (or None); used to look up the current
        # action's per-pose images so code_for can inject the pose globals (pose level).
        self._poses_provider = poses_provider
        self._data: dict = builtin_patches_data()
        # A manual screen override (set by the SET_SCREEN_PATCH command): when non-None,
        # it is what the tool screen / render page show, taking precedence over the
        # zone/mode/action match. It is a live control (not persisted to patches.json);
        # the state frame carries it via code_for so every display client follows it.
        self._screen_override: str | None = None
        # Render-page UI state: {hud: bool, code: bool}. The HUD is the
        # connection/status overlay; "code" is the PressStart2P code overlay
        # that mirrors the live patch. Toggled from the editor's patches tab
        # (SET_RENDER_UI) and pushed in every state frame.
        self._render_ui: dict = {"hud": True, "code": False}
        self.reload()

    def current_data(self) -> dict:
        """The currently loaded patches table (for the HTTP GET fallback)."""
        return self._data

    def reload(self) -> None:
        """Re-read the patches file via the loader.

        On failure keep the current table (logged); on success swap the table and
        publish :data:`Event.PATCHES_CHANGED`.
        """
        try:
            data = self._patches_loader()
        except (OSError, ValueError) as exc:
            print(f"[patches] reload failed ({exc}); keeping current table")
            return
        self._data = data
        self.bus.publish(Event.PATCHES_CHANGED, "patches")
        print(f"[patches] reloaded: zones={len(data.get('zones', {}))} "
              f"modes={len(data.get('modes', {}))} "
              f"actions={len(data.get('actions', {}))}")

    def code_for(self, zone, mode, action, pose_index=None, behavior=None) -> str:
        """The hydra code for a state (used by the state frame and the /api/screen endpoint).

        A manual screen override (set via :meth:`set_screen_override`) wins over the
        concatenation — it is the patch the operator has pushed to the screens.
        Otherwise the three parts are concatenated in order:

        1. **Zone patch** (``zones[zone]``) — renders to ``o0``.
        2. **Action/default patch** (``actions[zone][action]`` or ``default``) —
           renders to ``o1``, with the pose globals (``pose`` + ``poseindex``)
           injected when the state is inside an action that has per-pose images.
        3. **Behavior patch** (``modes[behavior]``) — renders to ``o3``.

        Missing parts are omitted; the parts are joined by newlines. When no part
        is available, :data:`DEFAULT_HYDRA_CODE` is returned.
        """
        if self._screen_override:
            return self._screen_override
        parts = []
        # 1. Zone patch (o0)
        if zone:
            zone_code = (self._data.get("zones") or {}).get(zone)
            if isinstance(zone_code, str) and zone_code:
                parts.append(zone_code)
        # 2. Action/default patch (o1), with pose globals injected
        action_code = None
        if zone and action:
            action_code = (self._data.get("actions") or {}).get(zone, {}).get(action)
        if not (isinstance(action_code, str) and action_code):
            action_code = self._data.get("default")
        if isinstance(action_code, str) and action_code:
            parts.append(inject_pose_globals(action_code, pose_index, self._pose_imgs(zone, action)))
        # 3. Behavior patch (o3)
        if behavior:
            beh_code = (self._data.get("modes") or {}).get(behavior)
            if isinstance(beh_code, str) and beh_code:
                parts.append(beh_code)
        if not parts:
            return DEFAULT_HYDRA_CODE
        return "\n".join(parts)

    def _pose_imgs(self, zone, action):
        """The current action's per-pose image filenames, or None (duck-typed lookup)."""
        if not zone or not action or self._poses_provider is None:
            return None
        try:
            zones = self._poses_provider()
            imgs = zones.action_pose_imgs(zone, action) if zones is not None else None
            return imgs if isinstance(imgs, list) and imgs else None
        except Exception:  # noqa: BLE001 - a bad provider must not break patch resolution
            return None

    def set_screen_override(self, code) -> None:
        """Set (or clear with ``None`` / an empty string) the manual screen override.

        The override is what the tool screen and the fullscreen render page show until it
        is changed again or cleared. It is a live control: it is held in memory and pushed
        to every display client via the state frame (see :meth:`code_for`), and it is not
        written to ``patches.json``.
        """
        self._screen_override = code if (isinstance(code, str) and code) else None
        print(f"[patches] screen override {'cleared' if self._screen_override is None else 'set'}")

    def set_render_ui(self, hud=None, code=None) -> None:
        """Set the render-page UI state (HUD and code-overlay visibility).

        ``hud`` controls the connection/status overlay; ``code`` controls the
        PressStart2P code overlay that mirrors the live patch. Either may be
        ``None`` to leave that flag unchanged. The state is pushed to every
        display client via the state frame (see :meth:`render_ui`).
        """
        if isinstance(hud, bool):
            self._render_ui["hud"] = hud
        if isinstance(code, bool):
            self._render_ui["code"] = code
        print(f"[patches] render ui: hud={self._render_ui['hud']} code={self._render_ui['code']}")

    def render_ui(self) -> dict:
        """The current render-page UI state (for the state frame)."""
        return dict(self._render_ui)


def make_patches(bus: StateBus, patches_loader, poses_provider=None) -> Patches:
    return Patches(bus, patches_loader, poses_provider=poses_provider)
