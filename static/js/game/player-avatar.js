import * as THREE from 'three';

// Athletes are built once per kit/appearance, then reused across team resets.
// The group origin is the feet; +Z is forward. Only explicit pose calls move
// limbs, so standing players never keep the render loop awake.
const geometryCache = new Map();
const numberCache = new Map();
const appearancePalette = [
  { skin: '#e6b18b', hair: '#32251e', cut: 0 },
  { skin: '#965f3e', hair: '#211b18', cut: 1 },
  { skin: '#5d3826', hair: '#171412', cut: 0 },
  { skin: '#c58b65', hair: '#3d2c21', cut: 2 },
  { skin: '#f0c5a5', hair: '#9d7548', cut: 1 },
  { skin: '#ad7854', hair: '#241c17', cut: 0 },
  { skin: '#73482e', hair: '#171412', cut: 2 },
  { skin: '#d4a17a', hair: '#60452d', cut: 1 },
];

function shared(resource) {
  resource.userData ??= {};
  resource.userData.playerShared = true;
  return resource;
}

const primitives = {
  box: shared(new THREE.BoxGeometry(1, 1, 1)),
  tube: shared(new THREE.CylinderGeometry(1, 1, 1, 6)),
  torso: shared(new THREE.CylinderGeometry(1, .84, 1, 10)),
  head: shared(new THREE.SphereGeometry(1, 12, 8)),
  hand: shared(new THREE.SphereGeometry(1, 6, 4)),
  hair: shared(new THREE.SphereGeometry(1, 12, 4, 0, Math.PI * 2, 0, Math.PI * .4)),
  plane: shared(new THREE.PlaneGeometry(1, 1)),
  ring: shared(new THREE.RingGeometry(.975, 1.055, 40)),
};
const clothingMaterial = shared(new THREE.MeshStandardMaterial({
  vertexColors: true, roughness: .84, metalness: 0,
}));
const selectionMaterial = shared(new THREE.MeshBasicMaterial({
  color: '#67e8f9', side: THREE.DoubleSide, depthWrite: false,
  transparent: true, opacity: .95, toneMapped: false,
}));
let shadowMaterial;

function finite(value, fallback = 0) {
  return Number.isFinite(Number(value)) ? Number(value) : fallback;
}
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export function playerRadius(stats, authoritativeRadius) {
  if (Number.isFinite(Number(authoritativeRadius)) && Number(authoritativeRadius) > 0) {
    return Number(authoritativeRadius);
  }
  const size = clamp(finite(stats?.size ?? 50, 50), 0, 100);
  return 12 + size * .16;
}

function colorHex(value) {
  return '#' + new THREE.Color(value ?? '#2563eb').getHexString();
}

function appearanceFor(seed, side) {
  const text = String(seed ?? 0) + ':' + String(side ?? 'a');
  let hash = 2166136261;
  for (const char of text) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  const index = (hash >>> 0) % appearancePalette.length;
  return { ...appearancePalette[index], index };
}

function part(geometry, color, position, scale, rotation = [0, 0, 0]) {
  const matrix = new THREE.Matrix4().compose(
    new THREE.Vector3(...position),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotation)),
    new THREE.Vector3(...scale),
  );
  return { geometry, color: new THREE.Color(color), matrix };
}

// Bake small kit, face, and boot details into vertex colors rather than giving
// each detail a separate mesh/material/draw call.
function mergeParts(parts) {
  const positions = [], normals = [], colors = [];
  for (const entry of parts) {
    const geometry = entry.geometry.index ? entry.geometry.toNonIndexed() : entry.geometry.clone();
    geometry.applyMatrix4(entry.matrix);
    positions.push(...geometry.attributes.position.array);
    normals.push(...geometry.attributes.normal.array);
    for (let i = 0; i < geometry.attributes.position.count; i++) {
      colors.push(entry.color.r, entry.color.g, entry.color.b);
    }
    geometry.dispose();
  }
  const result = shared(new THREE.BufferGeometry());
  result.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  result.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  result.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  result.computeBoundingBox();
  result.computeBoundingSphere();
  return result;
}

function kitFor(team, isKeeper, keeperColor) {
  const color = new THREE.Color(team);
  const jersey = isKeeper ? colorHex(keeperColor ?? (color.b > color.r ? '#d9b72b' : '#49b997')) : team;
  const shorts = isKeeper ? '#203039' : '#' + color.clone().multiplyScalar(.38).getHexString();
  return { jersey, shorts, socks: jersey, trim: '#f4f5ee', boots: '#171f27', sole: '#777e82' };
}

function bodyGeometry(kit, look) {
  const { box, tube, torso, head, hair } = primitives;
  const parts = [
    part(torso, kit.jersey, [0, 2.10, 0], [.56, 1.05, .34]),
    part(box, kit.shorts, [0, 1.62, 0], [.84, .19, .53]),
    part(tube, look.skin, [0, 2.65, 0], [.105, .18, .105]),
    part(head, look.skin, [0, 2.94, .015], [.225, .285, .235]),
    // Small ears and a nose give the profile a human silhouette.
    part(box, look.skin, [-.224, 2.93, .01], [.047, .093, .095]),
    part(box, look.skin, [.224, 2.93, .01], [.047, .093, .095]),
    part(box, look.skin, [0, 2.92, .239], [.055, .079, .052]),
    part(hair, look.hair, [0, 2.965, .002], [.232, look.cut === 2 ? .27 : .29, .247]),
    part(box, look.hair, [0, 2.99, -.202], [.34, .24, .074]),
    // Narrow eyes and brows, rather than oversized cartoon eyes.
    part(box, '#263035', [-.087, 2.99, .242], [.038, .022, .012]),
    part(box, '#263035', [.087, 2.99, .242], [.038, .022, .012]),
    part(box, look.hair, [-.087, 3.025, .23], [.045, .014, .014]),
    part(box, look.hair, [.087, 3.025, .23], [.045, .014, .014]),
    // A flat mouth sits in the skin instead of a protruding capsule.
    part(box, new THREE.Color(look.skin).multiplyScalar(.56), [0, 2.815, .231], [.059, .012, .01]),
    part(box, kit.trim, [-.24, 2.34, .322], [.065, .085, .018]),
    part(box, kit.trim, [-.085, 2.55, .323], [.19, .022, .018], [0, 0, -.3]),
    part(box, kit.trim, [.085, 2.55, .323], [.19, .022, .018], [0, 0, .3]),
  ];
  for (const sign of [-1, 1]) {
    parts.push(part(box, kit.shorts, [sign * .235, 1.48, 0], [.39, .28, .51]));
    parts.push(part(box, kit.trim, [sign * .473, 1.5, 0], [.018, .23, .42]));
    parts.push(part(box, kit.trim, [sign * .35, 2.59, 0], [.17, .018, .36]));
  }
  if (look.cut === 1) {
    parts.push(part(box, new THREE.Color(look.hair).multiplyScalar(.48), [.057, 3.215, .002], [.016, .01, .21]));
  }
  if (kit.official) {
    parts.push(
      part(box, '#29343b', [0, 2.44, .347], [.018, .27, .018]),
      part(box, '#26323a', [-.24, 2.31, .35], [.20, .13, .018]),
      part(box, '#26323a', [.24, 2.31, .35], [.20, .13, .018]),
      part(box, '#f4f3dc', [-.24, 2.32, .365], [.086, .09, .012]),
      // A neck lanyard and a compact dark whistle on the chest.
      part(tube, '#243038', [-.075, 2.40, .369], [.009, .34, .009], [0, 0, -.4]),
      part(tube, '#243038', [.075, 2.40, .369], [.009, .34, .009], [0, 0, .4]),
      part(box, '#28363f', [0, 2.21, .38], [.055, .073, .05]),
      // Radio earpiece, with a short boom beside the cheek.
      part(box, '#243038', [-.25, 2.95, .015], [.035, .075, .055]),
      part(tube, '#243038', [-.22, 2.87, .10], [.012, .15, .012], [-.8, 0, .3]),
    );
  }
  return mergeParts(parts);
}

function legGeometry(kit, look) {
  const { box, tube } = primitives;
  return mergeParts([
    part(tube, look.skin, [0, -.39, 0], [.145, .61, .15]),
    part(tube, look.skin, [0, -.92, .014], [.108, .57, .113]),
    part(tube, kit.socks, [0, -1.09, .014], [.115, .54, .119]),
    part(tube, kit.trim, [0, -.825, .014], [.117, .046, .12]),
    part(box, kit.boots, [0, -1.39, .105], [.23, .17, .4]),
    part(box, kit.sole, [0, -1.467, .105], [.235, .025, .405]),
    part(box, kit.trim, [0, -1.315, .17], [.045, .012, .16]),
  ]);
}

function armGeometry(kit, look, isKeeper) {
  const { box, tube, hand } = primitives;
  return mergeParts([
    part(tube, kit.jersey, [0, -.145, 0], [.145, .3, .15]),
    part(tube, kit.trim, [0, -.292, 0], [.147, .027, .153]),
    part(tube, isKeeper ? kit.jersey : look.skin, [0, -.52, .009], [.083, .48, .087]),
    part(hand, isKeeper ? '#e0e9e5' : look.skin, [0, -.82, .009], isKeeper ? [.135, .155, .10] : [.08, .112, .082]),
    ...(isKeeper ? [part(box, kit.shorts, [0, -.715, .009], [.17, .06, .13])] : []),
    ...(kit.official ? [
      part(tube, '#18252b', [0, -.71, .009], [.09, .067, .094]),
      part(box, '#83999f', [0, -.71, .103], [.068, .05, .017]),
    ] : []),
  ]);
}

function resources(kit, look, isKeeper) {
  const key = [kit.jersey, kit.shorts, look.index, isKeeper, !!kit.official].join('|');
  if (!geometryCache.has(key)) {
    geometryCache.set(key, {
      body: bodyGeometry(kit, look), leg: legGeometry(kit, look), arm: armGeometry(kit, look, isKeeper),
    });
  }
  return geometryCache.get(key);
}

const numberGeometry = (() => {
  const front = primitives.plane.clone();
  front.scale(.26, .32, 1); front.translate(0, 2.17, .354);
  const back = primitives.plane.clone();
  back.scale(.49, .59, 1); back.rotateY(Math.PI); back.translate(0, 2.17, -.354);
  const frontVertices = front.toNonIndexed(), backVertices = back.toNonIndexed();
  const geometry = shared(new THREE.BufferGeometry());
  for (const name of ['position', 'normal', 'uv']) {
    const a = frontVertices.attributes[name], b = backVertices.attributes[name];
    geometry.setAttribute(name, new THREE.Float32BufferAttribute([...a.array, ...b.array], a.itemSize));
  }
  front.dispose(); back.dispose(); frontVertices.dispose(); backVertices.dispose();
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  return geometry;
})();

function numberMaterial(number) {
  const label = String(number ?? 7).slice(0, 3);
  if (numberCache.has(label)) return numberCache.get(label);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 128;
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, 128, 128);
  context.font = `800 ${label.length > 2 ? 65 : 86}px Arial, sans-serif`;
  context.textAlign = 'center'; context.textBaseline = 'middle';
  context.lineJoin = 'round'; context.lineWidth = 7;
  context.strokeStyle = '#16202d'; context.strokeText(label, 64, 66);
  context.fillStyle = '#ffffff'; context.fillText(label, 64, 66);
  const texture = shared(new THREE.CanvasTexture(canvas));
  texture.colorSpace = THREE.SRGBColorSpace;
  const material = shared(new THREE.MeshStandardMaterial({
    map: texture, alphaTest: .35, roughness: .88, polygonOffset: true,
    polygonOffsetFactor: -1, polygonOffsetUnits: -1,
  }));
  numberCache.set(label, material);
  return material;
}

function getShadowMaterial() {
  if (shadowMaterial) return shadowMaterial;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 64;
  const context = canvas.getContext('2d');
  const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 32);
  gradient.addColorStop(0, 'rgba(0,0,0,.28)');
  gradient.addColorStop(.58, 'rgba(0,0,0,.16)');
  gradient.addColorStop(1, 'rgba(0,0,0,0)');
  context.fillStyle = gradient; context.fillRect(0, 0, 64, 64);
  const texture = shared(new THREE.CanvasTexture(canvas));
  shadowMaterial = shared(new THREE.MeshBasicMaterial({
    map: texture, transparent: true, depthWrite: false, toneMapped: false,
  }));
  return shadowMaterial;
}

function mesh(geometry, material, name) {
  const result = new THREE.Mesh(geometry, material);
  result.name = name; result.castShadow = true;
  return result;
}

export function createPlayerAvatar(teamColor, isKeeper = false, stats, number, options = {}) {
  const radius = playerRadius(stats, options.radius);
  const look = appearanceFor(options.seed ?? number ?? 0, options.side);
  const kit = options.role === 'referee'
    ? { jersey: colorHex(teamColor), shorts: '#182329', socks: '#182329', trim: '#26363b',
        boots: '#141b20', sole: '#637177', official: true }
    : kitFor(colorHex(teamColor), isKeeper, options.keeperColor);
  const shapes = resources(kit, look, isKeeper);
  const group = new THREE.Group(); group.name = 'footballer';
  const athlete = new THREE.Group(); athlete.name = 'athlete';
  athlete.scale.setScalar(radius * .7); group.add(athlete);
  const body = mesh(shapes.body, clothingMaterial, 'kit-and-head'); athlete.add(body);
  const legs = [], arms = [];
  for (const sign of [-1, 1]) {
    const leg = mesh(shapes.leg, clothingMaterial, sign < 0 ? 'left-leg' : 'right-leg');
    leg.position.set(sign * .235, 1.49, 0); athlete.add(leg); legs.push(leg);
    const arm = mesh(shapes.arm, clothingMaterial, sign < 0 ? 'left-arm' : 'right-arm');
    arm.position.set(sign * .56, 2.45, 0); arm.rotation.z = sign * .12;
    athlete.add(arm); arms.push(arm);
  }
  if (!kit.official) athlete.add(mesh(numberGeometry, numberMaterial(number ?? (isKeeper ? 1 : 7)), 'shirt-number'));
  const shadow = mesh(primitives.plane, getShadowMaterial(), 'shadow');
  shadow.castShadow = false; shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = .09; shadow.scale.set(radius * 1.8, radius * 1.5, 1); group.add(shadow);
  const ring = mesh(primitives.ring, selectionMaterial, 'selection-ring');
  ring.castShadow = false; ring.rotation.x = -Math.PI / 2; ring.position.y = .16;
  ring.scale.setScalar(radius); ring.visible = false; group.add(ring);
  const pose = { stride: 0, kick: 0, lean: 0 };
  group.userData = {
    radius, top: 3.26 * radius * .7, ring,
    avatar: { appearance: look, kit, isKeeper, number: number ?? (isKeeper ? 1 : 7), body, legs, arms, athlete, pose },
    setSelected(selected) {
      const changed = ring.visible !== !!selected; ring.visible = !!selected; return changed;
    },
    setPose(next = {}) {
      const stride = clamp(finite(next.stride), -1, 1);
      const kick = clamp(finite(next.kick), 0, 1);
      const lean = clamp(finite(next.lean), -.25, .25);
      if (stride === pose.stride && kick === pose.kick && lean === pose.lean) return false;
      Object.assign(pose, { stride, kick, lean });
      legs[0].rotation.x = stride * .5 + kick * .12;
      legs[1].rotation.x = -stride * .5 - kick * .95;
      arms[0].rotation.x = -stride * .32 - kick * .15;
      arms[1].rotation.x = stride * .32 + kick * .2;
      athlete.rotation.x = lean;
      return true;
    },
  };
  return group;
}
