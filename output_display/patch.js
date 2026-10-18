// ============================================================================
//  FTI_kuka — output display
//  P5LIVE + Hydra video patch
//
//  What it does
//  - Preloads the 23 AI-generated videos in the display set.
//  - On MIDI note-on, selects a video (C4 = first) and one of 12 Hydra
//    treatments (one per semitone).
//  - The selected video is fed into Hydra source s0 and processed live.
//  - CC1..CC6 modulate the active treatment; CC1 is the most significant.
//
//  How to run
//  1. Put the 23 videos where P5LIVE can serve them (see VID_BASE below).
//  2. Open this sketch in P5LIVE (offline libs are bundled with P5LIVE).
//  3. Enable Web MIDI (browser permission) and connect the MIDI device.
//  4. Play a note at/above C4 (MIDI 60) to select a video + treatment.
//     Turn CC1..CC6 to modulate.
//
//  Mapping (C4 = MIDI 60)
//    video index = (note - 60) % 23   (wraps over the 23 clips)
//    patch index = (note - 60) % 12   (12 treatments, one per semitone)
//    c[i]        = ccs[i+1].value / 127   (CC1 -> c[0] = most significant)
// ============================================================================

// Offline libs shipped with P5LIVE (authoritative hydra-synth v-bundled + hy5).
// Add the online fallbacks if you ever run without the bundled includes:
//   let libs = ['https://unpkg.com/hydra-synth/dist/hydra-synth.js',
//               'includes/libs/hydra-synth.js',
//               'https://cdn.jsdelivr.net/gh/ffd8/hy5@main/hy5.js',
//               'includes/libs/hy5.js']
let libs = ['includes/libs/hydra-synth.js', 'includes/libs/hy5.js'];

// ---------------------------------------------------------------------------
//  Config
// ---------------------------------------------------------------------------

// Base URL for the 23 videos, relative to where P5LIVE serves the project.
// The clips currently live in  output_display/vids/  .
//   - If P5LIVE serves the sketch folder as the web root, use  'vids/'  .
//   - If it uses the standard p5  data/  asset convention, move the clips to
//     data/vids/  (or point VID_BASE there).
// Adjust this one line if neither resolves at runtime.
const VID_BASE = 'data/vids/';

// The 23 clips (numbering skips 00030). Listed explicitly so the gap is
// handled correctly — do NOT generate names with (22 + i).
const VID_FILES = [
  'MiniMax_H3_00022_.mp4',
  'MiniMax_H3_00023_.mp4',
  'MiniMax_H3_00024_.mp4',
  'MiniMax_H3_00025_.mp4',
  'MiniMax_H3_00026_.mp4',
  'MiniMax_H3_00027_.mp4',
  'MiniMax_H3_00028_.mp4',
  'MiniMax_H3_00029_.mp4',
  'MiniMax_H3_00031_.mp4',
  'MiniMax_H3_00032_.mp4',
  'MiniMax_H3_00033_.mp4',
  'MiniMax_H3_00034_.mp4',
  'MiniMax_H3_00035_.mp4',
  'MiniMax_H3_00036_.mp4',
  'MiniMax_H3_00037_.mp4',
  'MiniMax_H3_00038_.mp4',
  'MiniMax_H3_00039_.mp4',
  'MiniMax_H3_00040_.mp4',
  'MiniMax_H3_00041_.mp4',
  'MiniMax_H3_00042_.mp4',
  'MiniMax_H3_00043_.mp4',
  'MiniMax_H3_00044_.mp4',
  'MiniMax_H3_00045_.mp4',
];

const NUM_VIDS    = VID_FILES.length; // 23
const NUM_PATCHES = 12;               // one treatment per semitone
const NOTE_BASE   = 60;               // C4 — first playable note
const DEBUG       = false;            // log note/patch changes to console

// ---------------------------------------------------------------------------
//  State
// ---------------------------------------------------------------------------
let videos     = [];   // loaded p5 video objects (index -> clip)
let activeIdx  = -1;   // selected video index, or -1 until first note
let patchIdx   = 0;    // active treatment index (0..11)
let prevNote   = -1;   // last note-on seen (change detection)
let lastCC     = null; // last CC values used to rebuild the Hydra graph
let hud        = null; // on-screen status label (DOM, not the p5 canvas)

// ---------------------------------------------------------------------------
//  The 12 treatments (one per semitone)
//
//  Each takes c[] = [CC1..CC6] normalised to 0..1 (CC1 = c[0] = most
//  significant) and returns a Hydra output via the .out() chain.
//
//  Function signatures are the EXACT set from P5LIVE's bundled hydra-synth
//  (includes/libs/hydra-synth.js). Note: combine functions take a texture
//  argument as their second input (the _c1), e.g. blend(tex, amount),
//  mask(tex). `noize` is hy5's alias for hydra's noise (p5 owns `noise`).
// ---------------------------------------------------------------------------
const patches = [
  // 0  C4   base — brightness + contrast
  (c) => src(s0).brightness(-0.3 + 0.6 * c[0]).contrast(1 + c[1] * 2).out(),

  // 1  C#4  hue — hue / saturate / invert
  (c) => src(s0).hue(c[0] * 0.8).saturate(c[1]).invert(c[2]).out(),

  // 2  D4   poster — posterize + pixelate
  (c) => src(s0).posterize(2 + c[0] * 10, 0.5 + c[1]).pixelate(2 + c[2] * 30).out(),

  // 3  D#4  thresh — luminance threshold + saturate
  (c) => src(s0).thresh(0.5 - c[0] * 0.3, 0.02 + c[1] * 0.3).saturate(c[2]).out(),

  // 4  E4   kaleid — kaleidoscope + rotate
  (c) => src(s0).kaleid(2 + c[0] * 10).rotate(c[1] * 6.28318, c[2] * 0.3).out(),

  // 5  F4   tiled — repeat + scroll
  (c) => src(s0).repeat(1 + c[0] * 4, 1 + c[1] * 4)
          .scroll(c[3] * 0.5, c[4] * 0.5, c[5] * 0.2, c[2] * 0.2).out(),

  // 6  F#4  colorama — colorama + channel shift
  (c) => src(s0).colorama(c[0] * 0.1).shift(c[1] * 0.5, c[2] * 0.5, c[3] * 0.5).out(),

  // 7  G4   noise — blend animated noise onto the clip
  (c) => src(s0).blend(noize(5 + c[0] * 15, 0.1 + c[1] * 0.4), 0.2 + c[2] * 0.8).out(),

  // 8  G#4  gradient — blend a moving gradient + hue
  (c) => src(s0).blend(gradient(c[2] * 0.5), 0.3 + c[0] * 0.7).hue(c[1] * 0.8).out(),

  // 9  A4   mask — clip the video to a rotating polygon
  (c) => src(s0).mask(shape(3 + c[0] * 9, 0.4 + c[1] * 0.3, 0.01)).hue(c[2] * 0.5).out(),

  // 10 A#4  luma — invert + luminance key + brightness
  (c) => src(s0).invert(c[0]).luma(0.5, c[1] * 0.5).brightness(-0.2 + 0.4 * c[2]).out(),

  // 11 B4   full — rotate / scale / hue / posterize / invert / brightness
  (c) => src(s0).rotate(c[0] * 6.28318).scale(1 + c[1]).hue(c[2] * 0.8)
          .posterize(2 + c[3] * 10).invert(c[4]).brightness(-0.3 + 0.6 * c[5]).out(),
];

// Standby visual (before the first note): an animated Hydra field that still
// responds to CCs, so the controls are visible before a note is played.
const standby = (c) =>
  voronoi(3 + c[0] * 8, 0.15 + c[1] * 0.4, 0.4 + c[2] * 0.5).hue(c[3] * 0.8).out();

// ---------------------------------------------------------------------------
//  p5 lifecycle
// ---------------------------------------------------------------------------
function preload() {
  // Load all 23 clips up front (muted, looping). Only the active clip is
  // allowed to play; the rest are paused to keep decode cost down.
  for (let i = 0; i < NUM_VIDS; i++) {
    const v = loadVideo(VID_BASE + VID_FILES[i]);
    v.mute();
    v.loop();
    v.pause();
    videos.push(v);
  }
}

function setup() {
  createCanvas(windowWidth, windowHeight);

  // hy5 bridge: feed the (hidden) p5 canvas into Hydra source s0, then hide
  // the p5 canvas so the Hydra output is what's on screen.
  s0.initP5();
  P5.toggle(0);

  // Optional retina resolution for the Hydra canvas (off by default for
  // performance with 23 clips). Enable for sharper output:
  // H.pixelDensity(2);

  // MIDI: channel 0, note range enabled.
  setupMidi(0, 0, true);
  // midi5.debug = ['note', 'controlchange']; // uncomment to trace MIDI

  // On-screen status label (DOM element, since the p5 canvas is hidden).
  hud = document.createElement('div');
  hud.style.position = 'fixed';
  hud.style.left = '12px';
  hud.style.bottom = '12px';
  hud.style.font = '12px monospace';
  hud.style.color = 'rgba(255,255,255,0.7)';
  hud.style.pointerEvents = 'none';
  hud.style.zIndex = '9999';
  document.body.appendChild(hud);
}

function draw() {
  updateMidi();

  // --- note-on: select video + treatment ----------------------------------
  if (typeof note === 'number' && note !== prevNote && note >= NOTE_BASE) {
    selectVideo((note - NOTE_BASE) % NUM_VIDS);
    patchIdx = (note - NOTE_BASE) % NUM_PATCHES;
    prevNote = note;
    if (DEBUG) console.log('note', note, '-> vid', activeIdx, 'patch', patchIdx);
  }

  // --- CC1..CC6 -> c[0..5] (CC1 most significant) -------------------------
  const c = readCCs();
  const changed = isCCChanged(c);

  if (activeIdx < 0) {
    // Standby: no clip selected yet. Rebuild only when CCs change; the
    // Hydra field keeps animating on its own in between.
    if (changed) standby(c);
  } else {
    // Feed the selected clip frame onto the p5 canvas (s0 reads it, dynamic).
    drawingContext.drawImage(videos[activeIdx].elt, 0, 0, width, height);
    // Rebuild the Hydra graph only when a CC or the patch changes; the
    // pipeline keeps running (and s0 keeps pulling the video) in between.
    if (changed) patches[patchIdx](c);
  }

  if (changed) lastCC = c.slice();
  updateHud();
}

// ---------------------------------------------------------------------------
//  Helpers
// ---------------------------------------------------------------------------
function readCCs() {
  const c = [0, 0, 0, 0, 0, 0];
  for (let i = 0; i < 6; i++) {
    const ccObj = ccs[i + 1]; // ccs[1] = CC1 .. ccs[6] = CC6
    if (ccObj && typeof ccObj.value === 'number') {
      c[i] = ccObj.value / 127;
    }
  }
  return c;
}

// True when the CC values differ from the last rebuild (or first build).
function isCCChanged(c) {
  if (!lastCC) return true;
  for (let i = 0; i < c.length; i++) {
    if (lastCC[i] !== c[i]) return true;
  }
  return false;
}

function selectVideo(idx) {
  if (activeIdx >= 0 && activeIdx < videos.length) {
    videos[activeIdx].pause();
  }
  activeIdx = idx;
  const v = videos[idx];
  v.loop();
  v.time(0);
  v.play();
}

function updateHud() {
  if (!hud) return;
  const cc = readCCs().map((x) => Math.round(x * 127));
  const label = activeIdx < 0
    ? 'standby'
    : `n${prevNote}  vid ${activeIdx}  patch ${patchIdx}`;
  hud.textContent = `${label}   cc[${cc.join(' ')}]`;
}

function windowResized() {
  resizeCanvas(windowWidth, windowHeight);
}
