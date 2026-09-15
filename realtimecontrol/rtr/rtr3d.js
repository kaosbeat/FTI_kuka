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
  const HOME = [0, -90, 90, 0, 90, 0];

  // Real KUKA colors: base + wrist black (RAL 9005), links orange (RAL 2003).
  const COLOR_BLACK = 0x0e0e10;
  const COLOR_ORANGE = 0xf67828;
  const COLOR_GHOST = 0x4fc08d;

  let scene, camera, renderer, controls, rotors, rotorsT, toolNode;
  let container, toolEl, logFn, running = false;

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
  function buildArm(geos, matFor) {
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
    tool.add(new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.09, 0.14), matFor("tool")));
    return { rot, tool };
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

  function init(containerEl, opts) {
    opts = opts || {};
    logFn = opts.log || console.log;
    if (typeof THREE === "undefined") { logFn("three.js failed to load; 3d view disabled"); return; }
    container = containerEl;
    toolEl = opts.toolEl || null;
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
      renderer.render(scene, camera);
      if (toolNode && toolEl) {
        const p = toolNode.getWorldPosition(new THREE.Vector3());
        toolEl.textContent = p.x.toFixed(2) + ", " + p.y.toFixed(2) + ", " + p.z.toFixed(2);
      }
    })();

    // Load the real KR60 meshes and build the arms (shared geometry).
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
      const cur = buildArm(geos, curMat);
      rotors = cur.rot; toolNode = cur.tool;
      const tgt = buildArm(geos, ghostMat);
      rotorsT = tgt.rot;
      setJoints(rotors, HOME);
      setJoints(rotorsT, HOME);
      running = true;
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

  return { init, update, setTarget };
})();
