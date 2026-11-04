# Harmonize the visuals system — leak-free video + complex patches

## Goal
Stop the browser memory crash in the render page (and editor previews) by making every
`initVideo` reuse a shared, preloaded video source instead of creating a new `<video>`
element + texture on every hydra re-eval. Keep the core generating the hydra code (no
protocol change). Keep the complex layered-patch system working, integrated with the video
system through one shared registry.

## Context / current state
- The render page shows ONE hydra patch, resolved by the core and pushed in every state
  frame (`patch` field, `rtr/display/display.py:151`).
- Resolution order (`rtr/patches/patches.py` `code_for`): **screen override** >
  **video mode** (per-pose fullscreen video, action-only) > **complex mode** (layered
  zone`o0` + action/default`o1` + behavior`o3`) > **default** (`osc(4,0.1,1.2).out()`).
- **Leak**: `initVideo` (`hydra-synth.js:2773`) creates a fresh `<video>` element + WebGL
  texture on every call; the render page re-evals the patch (`h.eval`, `render.html:92`)
  whenever the code string changes → unbounded growth → crash. The editor's two preview
  hydras (`editor.html:4327`, `:4487`) re-eval too.
- **Hydra source split**: `editor.html:883` already vendors the LOCAL `hydra-synth.js`;
  `render.html:57` and `client.html:252` load it from the **CDN**. To shim `initVideo`
  consistently, all pages must use a patchable local copy.
- **Two video dirs**: `assets/videos/` (new, video mode; 4 files) and `assets/vid/`
  (legacy; referenced by complex patches in `patches.json`).
- `zones.json` references ~30 unique video filenames; only 4 exist in `assets/videos/`
  → missing files must degrade gracefully (no crash, no retry storm).
- `Event.ZONES_CHANGED` (`engine.py:335`) is broadcast to pages; the render page currently
  does NOT listen for it.

## Decisions (confirmed with user)
1. **Keep the core generating the hydra code** — no change to the `patch` state field. The
   fix is client-side.
2. **Shim `initVideo`** to pull the `<video>` element from a shared registry keyed by URL,
   reusing it across evals.
3. **Eager preload** all referenced videos into the registry on startup + on `zones_changed`.
4. **Scope**: render page (video + complex modes) + the editor's two preview hydras.
   (`client.html` is a follow-up, same pattern.)

## Tasks
1. **Vendor hydra-synth.js for render + client.** Switch `render.html:57` and
   `client.html:252` from the CDN `<script>` to the local `hydra-synth.js` (match
   `editor.html:883`). Verify the local copy behaves identically (the editor already relies
   on it); if the version differs from what render/client currently get, update the local
   copy to match before switching.

2. **Patch the local `hydra-synth.js` `initVideo` (`:2773`)** to use a global registry
   (`window.__RTR_VIDS__`, keyed by the resolved video URL):
   - URL already registered → reuse the cached `<video>` element (set `this.src` to it; if
     it is already loaded, create the texture immediately without waiting for `loadeddata`;
     do not double-attach the load event).
   - Not registered → create the element, cache it, then proceed (current behavior).
   - Missing file: on `error`, mark the URL as missing in the registry (so we don't retry
     the 404 every eval) and leave the source empty (page keeps the last-good frame).
   - **Optional (bounds GPU memory):** also cache the regl texture per URL and reuse it, so
     repeated evals of the same video don't accumulate textures.

3. **Add a shared browser-side video registry + eager preload** (small JS reused by render
   + editor):
   - `preloadVideos()`: `GET /api/videos` → for each name create a `<video>`
     (`preload="auto"`, `muted`, `loop`, NOT autoplay) under `/assets/videos/<name>` into
     the registry. Stagger (small concurrency) to avoid a burst.
   - Trigger on page startup and on the `zones_changed` event.
   - Handle 404s: mark missing; no crash.

4. **Core: add `GET /api/videos` (list)** in `rtr/io/httpserver.py` returning the referenced
   video filenames — the union of all `pose_videos` in the zones table (the core already has
   `videos_path` + the zones table). Optionally include an `on_disk` flag per name.

5. **Wire the render page**: listen for `zones_changed` → re-preload; ensure the registry is
   populated (or the shim creates on first use) around the first patch eval. `renderCode`
   stays as-is (the shim handles reuse).

6. **Wire the editor previews** (`editor.html:4327` patch preview, `:4487` action-editor
   preview): they already use the local hydra file (so the shim applies automatically); add
   the same preload on startup + `zones_changed`.

7. **`client.html` (follow-up, same pattern):** switch to local hydra + preload + shim
   (currently out of primary scope).

## Validation
- `GET /api/videos` returns the referenced names (unit test the endpoint; assert it equals
  the union of all `pose_videos`).
- Browser check: drive a sequence of video poses; confirm the registry holds ONE `<video>`
  per unique video (not one per eval) → memory stays bounded.
- Complex-patch check: a layered zone/action/behavior patch still renders; its `initVideo`
  reuses the registry (no new element per eval).
- Missing-file check: a pose referencing a non-existent video degrades gracefully (no crash,
  no retry storm).
- Run the existing JS/Python tests for the httpserver + patches adapters.

## Risks / caveats
- **Local vs CDN hydra**: switching render/client to the local copy could change behavior if
  versions differ. Mitigate: verify parity first; the editor already proves the local copy
  works.
- **Texture residual**: reusing the `<video>` element removes the dominant leak (decode
  pipelines). WebGL textures still re-create per eval (accepted per the chosen "shim only"
  option). The optional per-URL texture cache (task 2) bounds this fully.
- **Eager preload burst**: ~30 files on `zones_changed`; stagger the fetches.
- **Bundled hydra file edit**: the `initVideo` section is readable (not minified); patch
  carefully and keep a backup of the file.

## Out of scope / follow-ups
- `client.html` (same shim) — task 7.
- Per-URL texture caching (full GPU bound) — optional in task 2.
- A persistent video layer (no re-eval) if the texture residual still causes issues — a larger
  rework, deferred.
