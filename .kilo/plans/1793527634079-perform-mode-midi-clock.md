# Perform Mode: MIDI-Clock-Driven Live Performance

## Context

The `rtr` realtime system has three control modes (`manual`/`auto`/`assertive`) in
`client.html`, no perform mode, and **no MIDI-clock (0xF8) handling** in the MIDI layer
(`midi.py`'s `_key_for` only maps CC/note/noteoff/program). Speed today is dispatched as
`move(target, machine.speed)`; the sim treats speed as degrees/second, the real KUKA as a
kukapy percentage. Goal: a `perform` mode that is MIDI-clock-driven and focused on realtime
reaction speed — each move is timed to arrive in a set number of beats, with a
CC-controllable time multiplier, a dedicated `perform.html` live page, and an `editor.html`
perform tab for planning.

## Resolved Decisions (interviewed)

1. **Two CCs** — CC-A sets the physical velocity (direct); CC-B sets the time multiplier
   (`1x/2x/4x/8x/16x`). Independent. CC numbers are learned in the editor (not hardcoded).
2. **Clock** — external MIDI clock (0xF8) when present; otherwise internal BPM (default 130).
3. **Base beat count** — per-action in `zones.json` (`base_beats` field) + a global default.
4. **Deadline** — `base_beats × multiplier × (60 / BPM)` seconds.
5. **Time to arrive** — `distance / velocity`; distance = Σ|axis deltas| (degrees).
6. **Late handling** — **notify-only** (no auto-shortening). The move runs at CC-A velocity;
   a late alert fires when `time_to_arrive > deadline`. The planner flags late moves so they
   are not committed. The user fixes a late move by raising velocity (CC-A), extending the
   multiplier (CC-B), or lowering BPM.

## Ordered Task List

1. **BeatClock** (new `rtr/core/clock.py`): owns beat timing. External mode derives BPM from
   24 × 0xF8 clocks per quarter note; internal mode runs from a manual BPM (default 130).
   Falls back to internal on a clock timeout. Smooths the external BPM with a rolling
   average. Publishes beat state (current beat, BPM, source, beat_duration) to the state
   frame. Ticked by the engine at 20 Hz.
2. **MIDI clock + perform CCs** (modify `rtr/io/midi.py`): detect 0xF8 in the rtmidi callback
   and forward it to the BeatClock. Add a `perform` section to `midi.json` holding the CC-A
   (velocity) and CC-B (multiplier) keys, learned in the editor like the existing
   nav/capture/group keys.
3. **Machine perform state** (modify `rtr/state/machine.py`): add `perform` as a
   `control_mode` value (alongside manual/auto/assertive in `set_control_mode`). Add
   perform-mode state (velocity, multiplier, beat budget). On each move, compute the deadline
   and time-to-arrive; expose the late flag. While in perform mode the brain's autonomy is
   gated off (the operator drives moves via MIDI/UI).
4. **Engine** (modify `rtr/core/engine.py`): create and tick the BeatClock. On each move
   dispatch, compute deadline/time-to-arrive and publish the late state + beat state to the
   state frame.
5. **Zones** (modify `rtr/state/zones.py` + `zones.json`): add `base_beats` per action; a
   global default in the perform config.
6. **Perform config** (new `perform.json`): global default `base_beats`, internal BPM (130),
   velocity range (CC-A 0-127 → degrees/sec mapping), multiplier values (CC-B 0-127 →
   1x/2x/4x/8x/16x banded mapping).
7. **perform.html** (new `realtimecontrol/rtr/perform.html`): live controls — MIDI in/out
   settings, performance-zone selection, CC-A velocity slider, CC-B multiplier buttons
   (1x/2x/4x/8x/16x), BPM (internal value / external clock state), beat-clock state, late
   alerts, live move state (current zone/action + time-to-arrive).
8. **editor.html perform tab** (modify `realtimecontrol/rtr/editor.html`): planning — set
   `base_beats` per action, show time-to-arrive per move at a changeable BPM (default 130),
   flag late moves (red when `time_to_arrive > deadline`).
9. **HTTP server** (modify `rtr/io/httpserver.py`): serve `perform.html`; add a `/api/perform`
   endpoint for the perform config (get/set).

## Risks

- **Real-robot velocity**: kukapy speed is a percentage, not a known degrees/sec; the
  planner's estimate is precise for the sim but approximate for the real robot. Mitigation:
  the planner uses the sim's deterministic model; note the approximation in the UI.
- **Clock jitter**: 0xF8 ticks may be jittery; BeatClock smooths the BPM with a rolling
  average before it drives the deadline.
- **CC numbers not fixed**: CC-A and CC-B are learned in the editor (not hardcoded) so the
  operator maps them to their hardware.

## Validation

- **BeatClock**: feed a 0xF8 clock stream and verify BPM derivation; verify the internal
  clock (BPM 130) beat timing; verify fallback to internal on clock timeout.
- **CC mapping**: verify CC-A → velocity and CC-B → multiplier (1x/2x/4x/8x/16x).
- **Late detection**: verify the late flag fires when `time_to_arrive > deadline`.
- **Planner**: verify time-to-arrive at a chosen BPM and the late flag per move.
- **UI**: verify `perform.html` live controls and the `editor.html` perform tab.
- Run the existing test suite + lint/type checks for the touched Python modules.

## Open Questions

- None (all key decisions resolved).
