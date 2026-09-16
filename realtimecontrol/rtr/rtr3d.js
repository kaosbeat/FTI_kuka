// rtr3d.js — shared 3D model builder + helpers, used by client.html and editor.html.
//
// Builds the real KR60 from the kr60ha ROS model: the `kr60ha_macro.xacro` joint
// chain (base at the origin, A1 about -Z) driving the visual STL meshes in
// `assets/kr60ha/visual/`. Each KUKA joint variable (deg) drives one rotor about the
// xacro joint axis (the FK convention). This file expects THREE + OrbitControls to be
// loaded globally (three.js r128 non-module builds), plus optionally THREE.GLTFLoader
// (the Blender-editable environment) and THREE.STLLoader (the KR60 meshes).
//
// Usage:
//   Robot3D.init(containerEl, { toolEl, log, environment, environmentUrl });
//   Robot3D.update(snapshot);   // snapshot has .joints and .target

"use strict";

// Number formatter shared by both pages (one decimal, em-dash for null/undefined).
const fmt = (v) => v == null ? "—" : (Math.round(v * 10) / 10).toFixed(1);

// ------------------------------------------------------------------
// 3D model (Three.js). The frame chain mirrors `kr60ha_macro.xacro` (meters, Z-up):
// six revolute joints + a fixed tool0 ($FLANGE) frame. The visual STL meshes are
// loaded once and shared between the current and target (ghost) arms.
// ------------------------------------------------------------------
const Robot3D = (() => {
  // Revolute joints from kr60ha_macro.xacro: origin offset (xyz), frame orientation
  // (rpy, identity for this chain), and the rotation axis. A1 rotates about -Z.
  const JOINTS = [
    { xyz: [0, 0, 0],           rpy: [0, 0, 0],      axis: [0, 0, -1] },  // A1
    { xyz: [0.35, 0, 0.815],    rpy: [0, 0, 0],      axis: [0, 1, 0]  },  // A2
    { xyz: [0.85, 0, 0],        rpy: [0, 0, 0],      axis: [0, 1, 0]  },  // A3
    { xyz: [0.465, 0, 0.145],   rpy: [0, 0, 0],      axis: [-1, 0, 0] },  // A4
    { xyz: [0.355, 0, 0],       rpy: [0, 0, 0],      axis: [0, 1, 0]  },  // A5
    { xyz: [0.17, 0, 0],        rpy: [0, 0, 0],      axis: [-1, 0, 0] },  // A6
  ];
  // Fixed tool0 frame ($FLANGE) — a child of link_6, +90° about Y.
  const TOOL0 = { xyz: [0, 0, 0], rpy: [0, 1.5708, 0] };
  const MESH_NAMES = ["base_link", "link_1", "link_2", "link_3", "link_4", "link_5", "link_6"];
  const ASSET_DIR = "assets/kr60ha/visual/";
  // The Blender-editable tool GLB (authored Y-up like environment.glb). Replaces the
  // old white placeholder box at the $FLANGE; see assets/make_tool.py for the starter.
  const TOOL_ASSET_URL = "assets/tool.glb";
  const HOME = [0, -90, 90, 0, 90, 0];

  // Real KUKA colors: base + wrist black (RAL 9005), links orange (RAL 2003).
  const COLOR_BLACK = 0x0e0e10;
  const COLOR_ORANGE = 0xf67828;
  const COLOR_GHOST = 0x4fc08d;

  // The tool-screen resolution [width, height] — the hydra canvas mapped onto the tool's
  // red mesh. This is the size hydra screen content is rendered at, so when designing
  // patches, design to this resolution. Overridable per init via opts.screenResolution.
  const SCREEN_RESOLUTION = [512, 512];

  let scene, camera, renderer, controls, rotors, rotorsT, toolNode;
  let container, toolEl, logFn, running = false;
  // The tool-screen texture (fed by hydra); re-uploaded from the hydra canvas each frame.
  let screenTex = null;
  let screenW = SCREEN_RESOLUTION[0], screenH = SCREEN_RESOLUTION[1];

  function rotFromRpy(r) {
    const Rx = new THREE.Matrix4().makeRotationX(r[0]);
    const Ry = new THREE.Matrix4().makeRotationY(r[1]);
    const Rz = new THREE.Matrix4().makeRotationZ(r[2]);
    const RyRx = new THREE.Matrix4().multiplyMatrices(Ry, Rx);
    return new THREE.Matrix4().multiplyMatrices(Rz, RyRx);
  }

  // A material factory: returns a material for a named mesh of this arm.
  function makeMats(kind) {
    if (kind === "ghost") {
      const m = new THREE.MeshStandardMaterial({
        color: COLOR_GHOST, metalness: 0.1, roughness: 0.8,
        transparent: true, opacity: 0.22,
      });
      return () => m;
    }
    // Current arm: real KUKA colors.
    const black = new THREE.MeshStandardMaterial({ color: COLOR_BLACK, metalness: 0.4, roughness: 0.55 });
    const orange = new THREE.MeshStandardMaterial({ color: COLOR_ORANGE, metalness: 0.3, roughness: 0.5 });
    const tool = new THREE.MeshStandardMaterial({ color: 0xd7dbe2, metalness: 0.3, roughness: 0.6 });
    return (name) => {
      if (name === "base_link" || name === "link_6") return black;
      if (name === "tool") return tool;
      return orange; // link_1..link_5
    };
  }

  // Build one arm (six rotors + link meshes + tool0 marker) from shared geometries.
  // ``geos[name]`` is the STL BufferGeometry (or absent, in which case a placeholder
  // box keeps the arm visible). The base_link pedestal is added separately (it is static).
  // ``toolObj`` is the loaded tool GLB scene (or absent); it is cloned onto the tool
  // node. For the ghost arm its materials are swapped to the translucent green so the
  // target tool reads like the ghost links.
  function buildArm(geos, matFor, toolObj, isGhost) {
    const rot = [];
    for (let j = 0; j < 6; j++) {
      const jt = JOINTS[j];
      const rotor = new THREE.Group();
      rotor.position.set(jt.xyz[0], jt.xyz[1], jt.xyz[2]);
      (j === 0 ? scene : rot[j - 1]).add(rotor);
      rot.push(rotor);
      const name = "link_" + (j + 1);
      if (geos[name]) {
        rotor.add(new THREE.Mesh(geos[name], matFor(name)));
      } else {
        const ph = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.14, 0.3), matFor(name));
        rotor.add(ph);
        logFn(name + ".stl not loaded; placeholder link");
      }
    }
    // tool0 marker ($FLANGE) — a child of rotor[5]; drives the tool readout.
    const tool = new THREE.Group();
    tool.position.set(TOOL0.xyz[0], TOOL0.xyz[1], TOOL0.xyz[2]);
    tool.quaternion.setFromRotationMatrix(rotFromRpy(TOOL0.rpy));
    rot[5].add(tool);
    if (toolObj) {
      const t = toolObj.clone();
      if (isGhost) t.traverse((o) => { if (o.isMesh) o.material = matFor("tool"); });
      tool.add(t);
    } else {
      tool.add(new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.09, 0.14), matFor("tool")));
    }
    return { rot, tool };
  }

  // Swap the contents of a tool node (placeholder box vs the loaded GLB). Used to attach
  // the tool GLB *after* the arms are already built, so the tool load never gates motion.
  function attachTool(node, toolObj, matFor, isGhost) {
    while (node.children.length) node.remove(node.children[0]);
    const t = toolObj.clone();
    if (isGhost) t.traverse((o) => { if (o.isMesh) o.material = matFor("tool"); });
    node.add(t);
  }

  function setJoints(rot, jointsDeg) {
    for (let j = 0; j < 6; j++) {
      const ang = jointsDeg[j] * Math.PI / 180;
      const axis = new THREE.Vector3(JOINTS[j].axis[0], JOINTS[j].axis[1], JOINTS[j].axis[2]);
      rot[j].quaternion.setFromAxisAngle(axis, ang);
    }
  }

  // Load the real KR60 STL meshes (once each; the geometry is shared by both arms).
  // Failures are logged and skipped (e.g. file:// without the assets); ``onDone`` is
  // called with the geometry table once every load has settled.
  function loadAllMeshes(onDone) {
    const geos = {};
    let pending = MESH_NAMES.length;
    const finish = () => { if (--pending === 0) onDone(geos); };
    MESH_NAMES.forEach((name) => {
      new THREE.STLLoader().load(
        ASSET_DIR + name + ".stl",
        (geo) => { geos[name] = geo; finish(); },
        undefined,
        (err) => {
          logFn(name + ".stl not loaded: " + (err && err.message ? err.message : err));
          finish();
        }
      );
    });
  }

  // Load the Blender-editable environment (a GLB). glTF is Y-up; this scene is Z-up,
  // so the loaded scene is rotated +90° about X (maps +Y -> +Z) to sit on the floor.
  // Failures are logged and ignored (e.g. file:// without the assets).
  function loadEnvironment(url) {
    new THREE.GLTFLoader().load(url, (gltf) => {
      const env = gltf.scene;
      env.rotation.x = Math.PI / 2;
      scene.add(env);
    }, undefined, (err) => {
      logFn("environment not loaded: " + (err && err.message ? err.message : err));
    });
  }

  // Load the Blender-editable tool GLB (authored Y-up, like environment.glb). The loaded
  // scene is rotated +90° about X (maps +Y -> +Z) so it sits correctly in the Z-up robot
  // scene, then handed to buildArm to attach to the tool node. Failures are logged and
  // ``onDone(null)`` is called, so the white box placeholder is used instead.
  function loadToolAsset(url, onDone) {
    new THREE.GLTFLoader().load(url, (gltf) => {
      const t = gltf.scene;
      t.rotation.x = Math.PI / 2;
      onDone(t);
    }, undefined, (err) => {
      logFn("tool asset not loaded: " + (err && err.message ? err.message : err));
      onDone(null);
    });
  }

  // ------------------------------------------------------------------
  // Tool screen (hydra). The tool GLB carries a red mesh that stands in for a
  // screen. We render a hydra patch onto a dedicated canvas and use that canvas
  // as the screen mesh's texture, so the tool shows a live WebGL image.
  // ------------------------------------------------------------------

  // Built-in hydra patch used when the core's /api/screen is unreachable (e.g. the
  // page is opened from file://). Mirrors the placeholder in main.py.
  // const SCREEN_CODE_FALLBACK = "osc(4, 0.1, 1.2).out()";

  let vidurl = "http://127.0.0.1:8766/assets/vid/hand08.mp4"
  const SCREEN_CODE_FALLBACK = "s0.initVideo(vidurl); src(s0).out()";

  // Fetch the hydra patch from the core's HTTP API (same origin); fall back to the
  // built-in placeholder when the request fails.
  async function fetchScreenCode() {
    try {
      const r = await fetch("/api/screen", { cache: "no-store" });
      if (r.ok) {
        const data = await r.json();
        if (data && data.code) return data.code;
      }
    } catch (err) { /* keep the fallback */ }
    return SCREEN_CODE_FALLBACK;
  }

  // Find the first mesh whose material reads as red (the screen stand-in).
  function findRedMesh(obj) {
    let found = null;
    obj.traverse((o) => {
      if (found) return;
      if (o.isMesh && o.material && o.material.color) {
        const c = o.material.color;
        if (c.r > 0.5 && c.g < 0.3 && c.b < 0.3) found = o;
      }
    });
    return found;
  }

  // Render a hydra patch onto the tool's red mesh. ``toolGroup`` is the current
  // arm's tool node (the ghost arm's tool is already recoloured green, so only the
  // current tool has the red screen mesh). Failures are logged and skipped.
  function setupToolScreen(toolGroup) {
    if (!toolGroup) return;
    if (typeof Hydra === "undefined") {
      logFn("hydra-synth not loaded; tool screen stays red");
      return;
    }
    const mesh = findRedMesh(toolGroup);
    if (!mesh) { logFn("no red screen mesh found in tool"); return; }

    // A dedicated canvas hydra renders to. We pre-create its WebGL context with
    // preserveDrawingBuffer so three.js can reliably read the canvas as a texture
    // source each frame (regl reuses this context when it initialises).
    const canvas = document.createElement("canvas");
    canvas.width = screenW; canvas.height = screenH;
    canvas.getContext("webgl", { preserveDrawingBuffer: true });
    logFn("tool screen resolution: " + screenW + "x" + screenH);

    const hydra = new Hydra({ canvas, detectAudio: false, makeGlobal: true, autoLoop: true });

    // The screen texture: three.js re-uploads the canvas each frame via needsUpdate.
    const tex = new THREE.Texture(canvas);
    tex.needsUpdate = true;
    // The red screen mesh's UVs cover only a sub-rectangle of the texture (a central
    // crop), which made the 3D screen show a zoomed-in slice of the video. Remap the
    // texture so the mesh's UV range samples the full image, matching the standalone
    // previews (which display the whole canvas). Computed from the geometry so a
    // re-exported tool GLB still maps correctly.
    const uvAttr = mesh.geometry && mesh.geometry.attributes && mesh.geometry.attributes.uv;
    if (uvAttr) {
      let uMin = 1, uMax = -1, vMin = 1, vMax = -1;
      for (let i = 0; i < uvAttr.count; i++) {
        const u = uvAttr.getX(i), v = uvAttr.getY(i);
        if (u < uMin) uMin = u; if (u > uMax) uMax = u;
        if (v < vMin) vMin = v; if (v > vMax) vMax = v;
      }
      if (uMax > uMin && vMax > vMin) {
        tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
        tex.repeat.set(1 / (uMax - uMin), 1 / (vMax - vMin));
        tex.offset.set(-uMin / (uMax - uMin), -vMin / (vMax - vMin));
      }
    }
    screenTex = tex;
    mesh.material = new THREE.MeshBasicMaterial({ map: tex, color: 0xffffff });

    // Fetch the hydra patch (from the core) and run it.
    fetchScreenCode().then((code) => {
      try { hydra.eval(code); } catch (e) { logFn("hydra eval failed: " + e); }
    });
  }

  function init(containerEl, opts) {
    opts = opts || {};
    logFn = opts.log || console.log;
    if (typeof THREE === "undefined") { logFn("three.js failed to load; 3d view disabled"); return; }
    container = containerEl;
    toolEl = opts.toolEl || null;
    // Configurable tool-screen resolution (the hydra canvas mapped onto the red mesh).
    if (Array.isArray(opts.screenResolution) && opts.screenResolution.length === 2) {
      screenW = Math.max(1, opts.screenResolution[0] | 0);
      screenH = Math.max(1, opts.screenResolution[1] | 0);
    }
    const w = container.clientWidth || 600, h = container.clientHeight || 360;

    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d0f13);

    camera = new THREE.PerspectiveCamera(45, w / h, 0.01, 100);
    camera.position.set(2.2, -2.0, 1.6);
    camera.up.set(0, 0, 1);  // the kr60ha chain is Z-up

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(w, h);
    container.appendChild(renderer.domElement);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(0.3, 0, 0.9);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;
    controls.update();

    scene.add(new THREE.AmbientLight(0xffffff, 0.65));
    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(3, 4, 5);
    scene.add(dir);

    scene.add(new THREE.GridHelper(8, 16, 0x3a4152, 0x1c2029));

    if (opts.environment !== false && typeof THREE.GLTFLoader !== "undefined") {
      loadEnvironment(opts.environmentUrl || "assets/environment.glb");
    }

    // The render loop starts immediately (it renders the scene as it builds up).
    window.addEventListener("resize", () => {
      const cw = container.clientWidth, ch = container.clientHeight;
      if (!cw || !ch) return;
      camera.aspect = cw / ch;
      camera.updateProjectionMatrix();
      renderer.setSize(cw, ch);
    });
    (function loop() {
      requestAnimationFrame(loop);
      controls.update();
      if (screenTex) screenTex.needsUpdate = true;  // re-upload the hydra canvas
      renderer.render(scene, camera);
      if (toolNode && toolEl) {
        const p = toolNode.getWorldPosition(new THREE.Vector3());
        toolEl.textContent = p.x.toFixed(2) + ", " + p.y.toFixed(2) + ", " + p.z.toFixed(2);
      }
    })();

    // Load the real KR60 meshes, then build the arms (shared geometry).
    if (typeof THREE.STLLoader === "undefined") {
      logFn("STLLoader not available; 3d arm disabled");
      return;
    }
    loadAllMeshes((geos) => {
      const curMat = makeMats("cur");
      const ghostMat = makeMats("ghost");
      // Static base_link (the pedestal) — added once, shared by both arms.
      if (geos.base_link) {
        scene.add(new THREE.Mesh(geos.base_link, curMat("base_link")));
      } else {
        const ph = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.28, 0.5, 24), curMat("base_link"));
        ph.position.set(0, 0, 0.25);
        scene.add(ph);
        logFn("base_link.stl not loaded; placeholder pedestal");
      }
      // Build both arms now (with a placeholder tool) and mark the scene ready so the
      // robot moves immediately. The tool GLB + hydra screen are cosmetic add-ons: they
      // attach when they load and must NEVER gate the arm's motion — a slow or failed
      // tool load (or a hydra error) must not freeze the robot.
      const cur = buildArm(geos, curMat, null, false);
      rotors = cur.rot; toolNode = cur.tool;
      const tgt = buildArm(geos, ghostMat, null, true);
      rotorsT = tgt.rot;
      setJoints(rotors, HOME);
      setJoints(rotorsT, HOME);
      running = true;
      const attach = (toolObj) => {
        try {
          if (toolObj) {
            attachTool(cur.tool, toolObj, curMat, false);
            attachTool(tgt.tool, toolObj, ghostMat, true);
          }
          setupToolScreen(cur.tool);  // render the hydra screen onto the tool's red mesh
        } catch (e) {
          logFn("tool setup failed: " + (e && e.message ? e.message : e));
        }
      };
      if (typeof THREE.GLTFLoader !== "undefined") {
        loadToolAsset(TOOL_ASSET_URL, attach);
      } else {
        attach(null);
      }
    });
  }

  function update(s) {
    if (!running) return;
    if (s.joints) setJoints(rotors, s.joints);
    if (s.target) setJoints(rotorsT, s.target);
  }

  // Set only the target (ghost) arm — used by the editor to step through an
  // action's poses locally without the core.
  function setTarget(pose) {
    if (!running || !pose) return;
    setJoints(rotorsT, pose);
  }

  // The active tool-screen resolution [width, height] (the size hydra screen content is
  // rendered at). Exposed so the standalone patch previews (editor/client) can match it.
  function screenResolution() {
    return [screenW, screenH];
  }

  return { init, update, setTarget, screenResolution };
})();
