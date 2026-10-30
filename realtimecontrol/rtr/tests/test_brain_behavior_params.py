"""Tests for the per-behavior next-selection params (``stay`` / ``triggers`` /
``dwell_s`` / ``lock_duration_s``) surviving ``validate_brain_config``.

Regression: the editor can set ``dwell_s`` and ``lock_duration_s`` on ANY behavior
(the stay/advance trigger controls on the brain tab), and the runtime
(``brain._behavior_triggers_fired``) reads them off the behavior entry regardless of
the behavior's defaults. The validator previously kept only the keys present in the
behavior's ``DEFAULT_BEHAVIORS`` entry, so a saved brain.json with e.g.
``locked.lock_duration_s = 15`` or any ``dwell_s`` was silently stripped on
reload/hot-reload — the edits appeared "not saved".

Run directly (no pytest needed)::

    python tests/test_brain_behavior_params.py

or under pytest::

    pytest tests/test_brain_behavior_params.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ``rtr.core`` must load before ``rtr.brain.brain`` (a pre-existing circular
# import: rtr.core.__init__ -> rtr.core.engine -> rtr.brain.brain). Loading the
# package first resolves the cycle, matching how main.py boots the core.
import rtr.core  # noqa: F401

from rtr.brain.config import validate_brain_config, DEFAULT_BEHAVIORS


def _validate(behaviors):
    return validate_brain_config({"behaviors": behaviors})["behaviors"]


def test_lock_duration_s_kept_for_behavior_without_default():
    # ``locked`` has no ``lock_duration_s`` in its defaults — the editor can still
    # set it (the lock_duration trigger fires when the lock is held >= lock_duration_s).
    out = _validate({"locked": {"lock_duration_s": 15}})
    assert out["locked"]["lock_duration_s"] == 15.0


def test_lock_duration_s_kept_for_all_editor_behaviors():
    # Every behavior the editor exposes next-selection controls for must keep the
    # value, not just the ones with a built-in default (agitated/co/exhilarated/impatient).
    for name in DEFAULT_BEHAVIORS:
        out = _validate({name: {"lock_duration_s": 7}})
        assert out[name]["lock_duration_s"] == 7.0, name


def test_dwell_s_kept_for_all_editor_behaviors():
    # ``dwell_s`` is not in ANY behavior's defaults — it must still survive.
    for name in DEFAULT_BEHAVIORS:
        out = _validate({name: {"dwell_s": 2.5}})
        assert out[name]["dwell_s"] == 2.5, name


def test_default_backfill_when_absent():
    # No override: the defaults are filled so the runtime sees a complete section.
    out = _validate({})
    assert out["locked"] == DEFAULT_BEHAVIORS["locked"]
    # A behavior with a default lock_duration_s keeps its default when absent.
    assert out["agitated"]["lock_duration_s"] == 1.0
    # A behavior without one has no key (the runtime treats missing as "never fires").
    assert "lock_duration_s" not in out["locked"]
    assert "dwell_s" not in out["locked"]


def test_stay_and_triggers_still_validated():
    out = _validate({
        "restless": {"stay": 0.7, "triggers": ["dwell", "new_lock"], "wander_adj": 4.0},
    })
    assert out["restless"]["stay"] == 0.7
    assert out["restless"]["triggers"] == ["dwell", "new_lock"]
    assert out["restless"]["wander_adj"] == 4.0


def test_null_treated_as_absent():
    out = _validate({"locked": {"lock_duration_s": None, "dwell_s": None}})
    assert "lock_duration_s" not in out["locked"]
    assert "dwell_s" not in out["locked"]


def test_bad_types_rejected():
    for bad in ({"locked": {"dwell_s": "fast"}},
                {"locked": {"lock_duration_s": True}},
                {"locked": {"stay": "a lot"}},
                {"locked": {"triggers": "dwell"}}):
        try:
            _validate(bad)
            raise AssertionError(f"expected ValueError for {bad}")
        except ValueError:
            pass


def main():
    tests = [
        test_lock_duration_s_kept_for_behavior_without_default,
        test_lock_duration_s_kept_for_all_editor_behaviors,
        test_dwell_s_kept_for_all_editor_behaviors,
        test_default_backfill_when_absent,
        test_stay_and_triggers_still_validated,
        test_null_treated_as_absent,
        test_bad_types_rejected,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
