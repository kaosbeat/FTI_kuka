"""Patches adapter (hydra screen code).

The tool's screen (and the fullscreen render page) shows a hydra patch. The
patches are **data-driven**: they live in ``patches.json`` (edited in
``editor.html``, served/saved over ``/api/patches``, hot-reloaded). The built-in
:data:`DEFAULT_HYDRA_CODE` remains the fallback when the file is missing or
corrupt.

A patch is a **hydra-synth JS code string**. The table maps the core's state to
code:

- ``default`` — the code used when nothing else matches,
- ``zones``   — zone name -> code (zone entry),
- ``modes``   — mode name -> code (mode change),
- ``actions`` — two-level map ``zone name -> action name -> code`` (action start).

Match precedence (most specific wins): **action > mode > zone > default**.
The browser pages fetch the whole table once and match locally on every state
frame (no per-tick traffic); a ``PATCHES_CHANGED`` event tells them to
re-fetch after a hot-reload.
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
    if default is not None and (not isinstance(default, str) or not default):
        raise ValueError("'default' must be a non-empty string")
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
    """The hydra code for a state: action > mode > zone > default.

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


# ---------------------------------------------------------------------------
# The adapter.
# ---------------------------------------------------------------------------

class Patches:
    """Holds the hydra patch table; hot-reloaded from disk on request.

    The table is loaded from a callable ``patches_loader`` (which returns a
    validated patches table). :meth:`reload` re-reads it and publishes
    :data:`Event.PATCHES_CHANGED` so the display can tell the pages to re-fetch.
    """

    def __init__(self, bus: StateBus, patches_loader):
        self.bus = bus
        self._patches_loader = patches_loader
        self._data: dict = builtin_patches_data()
        # A manual screen override (set by the SET_SCREEN_PATCH command): when non-None,
        # it is what the tool screen / render page show, taking precedence over the
        # zone/mode/action match. It is a live control (not persisted to patches.json);
        # the state frame carries it via code_for so every display client follows it.
        self._screen_override: str | None = None
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

    def code_for(self, zone, mode, action) -> str:
        """The hydra code for a state (used by the /api/screen endpoint).

        A manual screen override (set via :meth:`set_screen_override`) wins over the
        zone/mode/action match — it is the patch the operator has pushed to the screens.
        """
        if self._screen_override:
            return self._screen_override
        return match_patch(self._data, zone, mode, action)

    def set_screen_override(self, code) -> None:
        """Set (or clear with ``None`` / an empty string) the manual screen override.

        The override is what the tool screen and the fullscreen render page show until it
        is changed again or cleared. It is a live control: it is held in memory and pushed
        to every display client via the state frame (see :meth:`code_for`), and it is not
        written to ``patches.json``.
        """
        self._screen_override = code if (isinstance(code, str) and code) else None
        print(f"[patches] screen override {'cleared' if self._screen_override is None else 'set'}")


def make_patches(bus: StateBus, patches_loader) -> Patches:
    return Patches(bus, patches_loader)
