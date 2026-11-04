// Node vm test for multipatch.js: runs the patch in a stubbed DOM/p5/Hydra
// sandbox and asserts (1) ALL videos are preloaded, (2) the note→video
// mapping (explicit NOTE_TO_VID entries + auto fallback + unmapped),
// (3) the midi5.noteOn trigger plays/pauses the right clips.
//
// The test is config-driven: it derives expectations from the patch's own
// VID_BASE / VIDS / NOTE_TO_VID / FIRST_NOTE, so it keeps working when the
// user edits the CONFIG section.
//
// Run: node test_multipatch.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, 'multipatch.js'), 'utf8');

// Capture the file's top-level bindings (const/let don't attach to the vm
// global object) so the test can reach the config + logic.
const code = src + `
;globalThis.__exports = {
  VID_BASE, VIDS, NOTE_TO_VID, FIRST_NOTE, AUTO_FALLBACK, LOOP,
  videoForNote, playVideo,
  get videos() { return videos; },
  get activeIdx() { return activeIdx; },
  preload, setup,
  midi5,
};
`;

function makeEl(tag) {
  return {
    tag,
    style: {},
    width: 0,
    height: 0,
    src: '',
    muted: false,
    loop: false,
    preload: '',
    playsInline: false,
    currentTime: 0,
    listeners: {},
    loaded: false,
    played: false,
    paused: false,
    load() { this.loaded = true; },
    play() { this.played = true; this.paused = false; },
    pause() { this.paused = true; },
    addEventListener(t, fn) { (this.listeners[t] || (this.listeners[t] = [])).push(fn); },
  };
}

const created = [];
const p5Canvas = makeEl('canvas');

const sandbox = {
  console,
  window: { innerWidth: 1000, innerHeight: 800 },
  document: {
    createElement(tag) { const el = makeEl(tag); created.push(el); return el; },
    body: { appendChild() {} },
  },
  Hydra: class {
    constructor(opts) { this.opts = opts; this.synth = { noise: () => 0 }; }
    setResolution() {}
  },
  // p5 globals the patch touches at top level / in setup():
  windowWidth: 1000,
  windowHeight: 800,
  width: 1000,
  height: 800,
  createCanvas() {},
  pixelDensity() {},
  resizeCanvas() {},
  drawingContext: { canvas: p5Canvas, drawImage() {} },
  s0: { init() {} },
  src: () => ({ out: () => {} }),
  o0: {},
  setupMidi() {},
  updateMidi() {},
  midi5: {},
};

vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'multipatch.js' });

const T = sandbox.__exports;

let pass = 0;
let fail = 0;
function t(name, cond) {
  if (cond) { pass++; }
  else { fail++; console.log('FAIL:', name); }
}

// Test preconditions: the config must have enough entries to exercise.
t('config has >= 2 videos', T.VIDS.length >= 2);
t('config has >= 2 explicit NOTE_TO_VID entries', Object.keys(T.NOTE_TO_VID).length >= 2);
if (T.VIDS.length < 2 || Object.keys(T.NOTE_TO_VID).length < 2) {
  console.log('config too small to test; aborting');
  process.exit(1);
}

// --- 1. preload(): ALL videos are buffered -------------------------------
T.preload();
t('preload created one <video> per VIDS entry', T.videos.length === T.VIDS.length);
const vidEls = created.filter((e) => e.tag === 'video');
t('every <video> was told to load (buffered now)', vidEls.every((v) => v.loaded));
t('every <video> is muted (browser autoplay policy)', vidEls.every((v) => v.muted));
t('every <video> points at VID_BASE + its filename',
  vidEls.every((v, i) => v.src === T.VID_BASE + T.VIDS[i]));

// --- 2. mapping: explicit NOTE_TO_VID entries ----------------------------
const entries = Object.entries(T.NOTE_TO_VID).map(([n, i]) => [Number(n), Number(i)]);
for (const [note, idx] of entries) {
  t(`note ${note} (explicit) -> VIDS[${idx}]`, T.videoForNote(note) === idx);
}

// --- 3. mapping: auto fallback for unmapped notes (wraps) ----------------
// (only exercised when AUTO_FALLBACK is on; skip cleanly when it is off)
if (T.AUTO_FALLBACK) {
  for (let k = 0; k < T.VIDS.length + 2; k++) {
    const n = T.FIRST_NOTE + k;
    if (n in T.NOTE_TO_VID) continue; // explicit entries win
    t(`note ${n} (auto) -> VIDS[${k % T.VIDS.length}]`, T.videoForNote(n) === k % T.VIDS.length);
  }
}

// --- 4. mapping: unmapped notes below FIRST_NOTE play nothing ------------
let unmappedBelow = T.FIRST_NOTE - 1;
while (unmappedBelow in T.NOTE_TO_VID && unmappedBelow > 0) unmappedBelow--;
t(`note ${unmappedBelow} (below FIRST_NOTE, unmapped) -> -1`, T.videoForNote(unmappedBelow) === -1);

// --- 5. MIDI trigger: note-on plays the mapped video ---------------------
T.setup();
t('setup wired midi5.noteOn', typeof sandbox.midi5.noteOn === 'function');
const [noteA, idxA] = entries[0];
const [noteB, idxB] = entries[1];

sandbox.midi5.noteOn({ type: 'noteon', number: noteA });
t(`noteon ${noteA} -> activeIdx ${idxA}`, T.activeIdx === idxA);
t(`video ${idxA} played from position 0`, T.videos[idxA].played && T.videos[idxA].currentTime === 0);

// --- 6. switching notes pauses the previous video ------------------------
sandbox.midi5.noteOn({ type: 'noteon', number: noteB });
t(`noteon ${noteB} -> activeIdx ${idxB}`, T.activeIdx === idxB);
t(`previous video (${idxA}) paused`, T.videos[idxA].paused);
t(`video ${idxB} played from position 0`, T.videos[idxB].played && T.videos[idxB].currentTime === 0);

// --- 7. note-off and unmapped notes leave playback untouched -------------
sandbox.midi5.noteOn({ type: 'noteoff', number: noteB });
t('noteoff leaves activeIdx unchanged', T.activeIdx === idxB);
sandbox.midi5.noteOn({ type: 'noteon', number: unmappedBelow });
t('unmapped note leaves activeIdx unchanged', T.activeIdx === idxB);

console.log(`${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
