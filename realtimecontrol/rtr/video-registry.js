// Shared, leak-free video source registry. initVideo() in hydra-synth.js reuses
// the <video> element cached here (keyed by absolute URL) instead of spawning a
// new element + texture on every hydra re-eval; preloadVideos() warms it eagerly.
(function (global) {
  "use strict";

  global.__RTR_VIDS__ = global.__RTR_VIDS__ || {};
  const BASE = "/assets/videos/";
  const CONCURRENCY = 3;

  // Absolute URL for a filename (patches.py generates initVideo("/assets/videos/<name>")).
  function urlFor(name) {
    if (!name) return "";
    return (name.indexOf("/") === 0) ? name : (BASE + name);
  }

  // Return (creating if needed) the shared <video> element for a URL.
  function ensureVideo(url) {
    const reg = global.__RTR_VIDS__;
    let e = reg[url];
    if (e && e.vid) return e;
    const vid = document.createElement("video");
    vid.crossOrigin = "anonymous";
    vid.autoplay = false;
    vid.loop = true;
    vid.muted = true;
    vid.preload = "auto";
    vid.src = url;
    e = e || {};
    e.vid = vid;
    reg[url] = e;
    vid.addEventListener("error", () => { e.missing = true; }, { once: true });
    return e;
  }

  function whenReady(e) {
    return new Promise((resolve) => {
      if (e.loaded || e.missing) return resolve();
      const v = e.vid;
      const done = () => { v.removeEventListener("loadeddata", on); v.removeEventListener("error", off); resolve(); };
      const on = () => { e.loaded = true; done(); };
      const off = () => { e.missing = true; done(); };
      v.addEventListener("loadeddata", on, { once: true });
      v.addEventListener("error", off, { once: true });
    });
  }

  // Eagerly warm the registry from the core's referenced-video list (staggered).
  async function preloadVideos() {
    let data = null;
    try {
      const r = await fetch("/api/videos", { cache: "no-store" });
      if (r.ok) data = await r.json();
    } catch (err) {
      console.log("video preload: /api/videos unavailable:", err);
      return;
    }
    if (!data || !Array.isArray(data.videos)) return;

    const reg = global.__RTR_VIDS__;
    const onDisk = Array.isArray(data.on_disk) ? new Set(data.on_disk) : null;

    // Referenced-but-absent files are marked missing up front (no 404 churn);
    // the rest are preloaded. Without on_disk, preload all and let 404s resolve.
    const urls = [];
    for (const name of data.videos) {
      const url = urlFor(name);
      if (onDisk && !onDisk.has(name)) {
        const e = reg[url] || {};
        e.missing = true;
        reg[url] = e;
      } else {
        urls.push(url);
      }
    }

    const queue = urls.slice();
    const worker = async () => {
      while (queue.length) {
        const url = queue.shift();
        await whenReady(ensureVideo(url));
      }
    };
    const pool = [];
    for (let i = 0; i < CONCURRENCY; i++) pool.push(worker());
    await Promise.all(pool);
  }

  global.__RTR_VID__ensure = ensureVideo;
  global.preloadVideos = preloadVideos;
})(typeof window !== "undefined" ? window : this);
