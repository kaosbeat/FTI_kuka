// noprotect  — disables P5LIVE's loopBreaker guard for this sketch
// ============================================================================
//  FTI_kuka — output display: multi-video MIDI patch
//
//  - Preloads ALL videos listed in VIDS below (raw HTML5 <video> elements,
//    buffered but paused — cheap to keep many clips ready at once).
//  - On MIDI note-on, plays the video mapped to that note (restarts it at 0).
//  - The mapping is one editable table: NOTE_TO_VID (below).
//
//  How to configure
//  1. Put your clips in output_display/vids/ (or edit VID_BASE).
//  2. List every clip you want in VIDS — every entry is preloaded.
//  3. Map notes to clips:
//       - explicit:   NOTE_TO_VID[47] = 2   →  note 47 plays VIDS[2]
//       - or rely on the auto mapping:  FIRST_NOTE + i  →  VIDS[i]
//         (wraps around at the end of VIDS; only used for notes that have
//          no explicit entry, and only when AUTO_FALLBACK is true).
//
//  How to run
//  1. Open this sketch in P5LIVE (offline hydra-synth is bundled with P5LIVE).
//  2. Enable Web MIDI (browser permission) and connect the MIDI device.
//  3. Press a mapped note → its clip plays. Press another → it switches.
//
//  Pattern sources: oldpatch.js (createVideo + midi5.noteOn trigger) and
//  patch.js (raw HTML5 video preload + robust Hydra setup for P5LIVE).
// ============================================================================

let libs = ['includes/libs/hydra-synth.js'];

// ---------------------------------------------------------------------------
//  CONFIG — edit here
// ---------------------------------------------------------------------------

// Base URL for the clips, relative to where P5LIVE serves this sketch.
const VID_BASE = 'vid/hands/';

// Every clip you want available. ALL of them are preloaded (buffered,
// paused). Order only affects the index numbers used below and in the HUD.
const VIDS = [
'hand01.mp4',
'hand02.mp4',
'hand03.mp4',
'hand04.mp4',
'hand05.mp4',
'hand06.mp4',
'hand07.mp4',
'hand08.mp4',

];

// MIDI note → video index.
//   Explicit entries win:  NOTE_TO_VID[47] = 2  →  note 47 plays VIDS[2].
//   Notes with no explicit entry fall back to the auto mapping when
//   AUTO_FALLBACK is true:  FIRST_NOTE + i  →  VIDS[i]  (wraps at the end).
const FIRST_NOTE    = 47;   // C3 — first auto-mapped note
const AUTO_FALLBACK = true; // false → unmapped notes play nothing
const NOTE_TO_VID = {
  47: 0,
  48: 1,
  49: 2,
  // 50: 0,   // example: note 50 loops back to the first clip
};

// Keep the clip looping until another note takes over?
// (false = play once, hold the last frame)
const LOOP = true;

const DEBUG = true;         // log note → video changes to the console

// ---------------------------------------------------------------------------
//  Hydra output canvas (built once, at sketch top level)
// ---------------------------------------------------------------------------
let RW = Math.floor(window.innerWidth);
let RH = Math.floor(window.innerHeight);
let hc = document.createElement('canvas');
hc.width = RW;
hc.height = RH;
hc.style.position = 'fixed';
hc.style.top = '0';
hc.style.left = '0';
hc.style.width = '100vw';
hc.style.height = '100vh';
hc.style.zIndex = '1';
document.body.appendChild(hc);

// Own Hydra instance (not the hy5 bridge) so we can turn OFF the defaults
// that trip P5LIVE: no mic getUserMedia(), no canvas.captureStream() recorder.
let hydra = new Hydra({
  detectAudio: false,          // no mic getUserMedia() at load
  enableStreamCapture: false,  // no canvas.captureStream(25) recorder
  makeGlobal: true,            // expose src, s0, out, noize, ... on window
  autoLoop: true,              // Hydra renders continuously on its own rAF
  canvas: hc                   // render to our fullscreen canvas
});

// p5 owns `noise`; hydra's noise texture is exposed as `noize`
// (hydra-synth convention). Alias it explicitly.
noize = hydra.synth.noise;

// ---------------------------------------------------------------------------
//  State
// ---------------------------------------------------------------------------
let videos    = [];   // raw HTML5 <video> elements, index -> clip
let activeIdx = -1;   // clip currently on screen, or -1 until first note
let hud       = null; // on-screen status label (DOM, since the p5 canvas is hidden)

// ---------------------------------------------------------------------------
//  p5 lifecycle
// ---------------------------------------------------------------------------
function preload() {
  // P5LIVE's bundled p5 is trimmed (it has createVideo but NO loadVideo),
  // so we build raw HTML5 <video> elements directly: they buffer reliably,
  // keep many clips ready, and draw straight onto the p5 canvas.
  // preload='auto' + load() = buffer every clip NOW (preload ALL videos).
  for (let i = 0; i < VIDS.length; i++) {
    const v = document.createElement('video');
    v.src = VID_BASE + VIDS[i];
    v.muted = true;          // required: browsers block unmuted autoplay
    v.loop = LOOP;
    v.preload = 'auto';      // buffer now
    v.playsInline = true;
    v.style.display = 'none';
    document.body.appendChild(v);
    v.load();                // start buffering, do not play
    videos.push(v);
  }
  if (DEBUG) console.log('preloaded', videos.length, 'videos');
}

function setup() {
  // Prevent retina up-scaling of the p5 canvas (the hidden Hydra source).
  pixelDensity(1);
  createCanvas(windowWidth, windowHeight);

  // Feed the (hidden) p5 canvas into Hydra source s0, then hide it so the
  // Hydra output (hc) is what's on screen. s0 is dynamic: it re-reads the
  // canvas every frame, so the drawn video updates the pipeline live.
  s0.init({ src: drawingContext.canvas, dynamic: true });
  drawingContext.canvas.style.display = 'none';

  // Static pass-through graph: the clip, 1:1. For a fancier treatment,
  // replace the line below (e.g. the oldpatch.js chain:
  //   src(o0).layer(src(s0)).scrollY(1.003).out(o0)
  // or any hydra-synth chain ending in .out(o0)).
  src(s0).out(o0);

  // MIDI (same API as oldpatch.js).
  setupMidi(0, 0);
  // midi5.debug = ['note', 'controlchange']; // uncomment to trace MIDI
  midi5.noteOn = (note) => {
    if (note.type === 'noteon') {
      const idx = videoForNote(note.number);
      if (idx >= 0) {
        playVideo(idx);
      } else if (DEBUG) {
        console.log('note', note.number, '-> no video mapped');
      }
    }
  };

  // On-screen status label (the p5 canvas is hidden).
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

  // Draw the active clip onto the hidden p5 canvas (s0 reads it, dynamic).
  // A paused/ended clip keeps showing its last frame.
  if (activeIdx >= 0) {
    drawingContext.drawImage(videos[activeIdx], 0, 0, width, height);
  }

  updateHud();
}

// ---------------------------------------------------------------------------
//  Mapping + playback
// ---------------------------------------------------------------------------

// MIDI note → video index, or -1 when the note plays nothing.
function videoForNote(n) {
  if (n in NOTE_TO_VID) return NOTE_TO_VID[n];
  if (AUTO_FALLBACK && n >= FIRST_NOTE) return (n - FIRST_NOTE) % VIDS.length;
  return -1;
}

// Play clip idx from the start; pause the previous one.
function playVideo(idx) {
  if (activeIdx >= 0 && activeIdx < videos.length) {
    videos[activeIdx].pause();
  }
  activeIdx = idx;
  const v = videos[idx];
  v.currentTime = 0; // restart from the beginning (like oldpatch.js)
  v.play();
  if (DEBUG) console.log('note -> vid', idx, VIDS[idx]);
}

function updateHud() {
  if (!hud) return;
  hud.textContent = activeIdx < 0
    ? `standby — ${VIDS.length} videos preloaded`
    : `vid ${activeIdx} (${VIDS[activeIdx]}) — ${VIDS.length} preloaded`;
}

function windowResized() {
  RW = Math.floor(window.innerWidth);
  RH = Math.floor(window.innerHeight);
  hc.width = RW;
  hc.height = RH;
  hydra.setResolution(RW, RH);
  resizeCanvas(windowWidth, windowHeight);
}
