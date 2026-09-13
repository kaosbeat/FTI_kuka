// rtr3d.js — shared 3D schematic builder + helpers, used by client.html and editor.html.
//
// Extracted from client.html so both pages share the same Robot3D (a schematic KR60
// built from the URDF joint chain) and the `fmt` number formatter. This file expects
// THREE and OrbitControls to be loaded globally (three.js r128 non-module builds), and
// optionally THREE.GLTFLoader (for the Blender-editable environment).
//
// Usage:
//   Robot3D.init(containerEl, { toolEl, log, environment, environmentUrl });
//   Robot3D.update(snapshot);   // snapshot has .joints and .target

"use strict";

// Number formatter shared by both pages (one decimal, em-dash for null/undefined).
const fmt = (v) => v == null ? "—" : (Math.round(v * 10) / 10).toFixed(1);

// ------------------------------------------------------------------
// 3D schematic (Three.js). The joint chain mirrors kuka_kr60.urdf; each rotor
// is driven by the KUKA joint variable (deg -> rad about the URDF joint axis).
// ------------------------------------------------------------------
const Robot3D = (() => {
  const JOINTS = [
    { pos: [0, 0, 0.47975],            rpy: [1.5708, 0, 0],          axis: [0, -1, 0] },
    { pos: [0.35, 0.33557, 0.094],     rpy: [-1.5708, 0, 0],         axis: [0, 1, 0] },
    { pos: [0.83287, -0.0097315, 0],   rpy: [0, 0, -0.011684],       axis: [0, 1, 0] },
    { pos: [0.60824, 0.089904, 0.145], rpy: [0, 0, 0],               axis: [-1, 0, 0] },
    { pos: [0.41176, 0, 0.0000638],    rpy: [0, -0.00079387, 0.011684], axis: [0.011683, 0.99993, 0] },
    { pos: [0.12607, -0.0000365, -0.0000281], rpy: [0, 0, 0],        axis: [-1, 0, 0] },
  ];
  const TOOL = { pos: [0.215, 0, 0], rpy: [0, 1.5708, 0] };
  const HOME = [0, -90, 90, 0, 90, 0];

  let scene, camera, renderer, controls, rotors, rotorsT, toolNode;
  let container, toolEl, logFn, running = false;

  function rotFromRpy(r) {
    const Rx = new THREE.Matrix4().makeRotationX(r[0]);
    const Ry = new THREE.Matrix4().makeRotationY(r[1]);
    const Rz = new THREE.Matrix4().makeRotationZ(r[2]);
    const RyRx = new THREE.Matrix4().multiplyMatrices(Ry, Rx);
    return new THREE.Matrix4().multiplyMatrices(Rz, RyRx);
  }

  function addSegment(parent, to, radius, mat) {
    const v = new THREE.Vector3(to[0], to[1], to[2]);
    const len = v.length();
    if (len < 1e-6) return;
    const geo = new THREE.CylinderGeometry(radius, radius * 0.8, len, 20);
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.copy(v.clone().multiplyScalar(0.5));
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), v.clone().normalize());
    parent.add(mesh);
  }

  function addSphere(parent, radius, mat) {
    parent.add(new THREE.Mesh(new THREE.SphereGeometry(radius, 20, 16), mat));
  }

  function buildArm(parent, mats) {
    const rot = [];
    let ref = parent;
    for (let j = 0; j < 6; j++) {
      const jt = JOINTS[j];
      const n = new THREE.Group();
      n.position.set(jt.pos[0], jt.pos[1], jt.pos[2]);
      n.quaternion.setFromRotationMatrix(rotFromRpy(jt.rpy));
      ref.add(n);
      const r = new THREE.Group();
      n.add(r);
      rot.push(r);
      const to = j < 5 ? JOINTS[j + 1].pos : TOOL.pos;
      addSegment(r, to, mats.linkRadius, mats.linkMat);
      addSphere(r, mats.jointRadius, mats.jointMat);
      ref = r;
    }
    const tool = new THREE.Group();
    tool.position.set(TOOL.pos[0], TOOL.pos[1], TOOL.pos[2]);
    tool.quaternion.setFromRotationMatrix(rotFromRpy(TOOL.rpy));
    ref.add(tool);
    tool.add(new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.09, 0.14), mats.toolMat));
    return { rot, tool };
  }

  function setJoints(rot, jointsDeg) {
    for (let j = 0; j < 6; j++) {
      const ang = jointsDeg[j] * Math.PI / 180;
      const axis = new THREE.Vector3(JOINTS[j].axis[0], JOINTS[j].axis[1], JOINTS[j].axis[2]);
      rot[j].quaternion.setFromAxisAngle(axis, ang);
    }
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
    camera.position.set(2.6, -2.4, 2.2);
    camera.up.set(0, 0, 1);  // URDF is Z-up

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(w, h);
    container.appendChild(renderer.domElement);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(0.5, 0, 1.1);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;
    controls.update();

    scene.add(new THREE.AmbientLight(0xffffff, 0.65));
    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(3, 4, 5);
    scene.add(dir);

    scene.add(new THREE.GridHelper(8, 16, 0x3a4152, 0x1c2029));

    const baseMat = new THREE.MeshStandardMaterial({ color: 0x2a2f3a, metalness: 0.2, roughness: 0.85 });
    const plate = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.7, 0.1), baseMat);
    plate.position.set(0, 0, 0.05);
    scene.add(plate);
    const ped = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.22, 0.48, 24), baseMat);
    ped.rotation.x = Math.PI / 2;
    ped.position.set(0, 0, 0.24);
    scene.add(ped);

    if (opts.environment !== false && typeof THREE.GLTFLoader !== "undefined") {
      loadEnvironment(opts.environmentUrl || "assets/environment.glb");
    }

    const curMats = {
      linkMat: new THREE.MeshStandardMaterial({ color: 0xff6a2a, metalness: 0.3, roughness: 0.5 }),
      jointMat: new THREE.MeshStandardMaterial({ color: 0x3a3f4a, metalness: 0.4, roughness: 0.6 }),
      toolMat: new THREE.MeshStandardMaterial({ color: 0xd7dbe2, metalness: 0.2, roughness: 0.7 }),
      linkRadius: 0.09, jointRadius: 0.11,
    };
    const tgtMats = {
      linkMat: new THREE.MeshStandardMaterial({ color: 0x4fc08d, metalness: 0.1, roughness: 0.8, transparent: true, opacity: 0.22 }),
      jointMat: new THREE.MeshStandardMaterial({ color: 0x4fc08d, metalness: 0.1, roughness: 0.8, transparent: true, opacity: 0.22 }),
      toolMat: new THREE.MeshStandardMaterial({ color: 0x4fc08d, metalness: 0.1, roughness: 0.8, transparent: true, opacity: 0.22 }),
      linkRadius: 0.09, jointRadius: 0.11,
    };

    const arm = buildArm(scene, curMats);
    rotors = arm.rot; toolNode = arm.tool;
    const armT = buildArm(scene, tgtMats);
    rotorsT = armT.rot;

    setJoints(rotors, HOME);
    setJoints(rotorsT, HOME);

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
    running = true;
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
