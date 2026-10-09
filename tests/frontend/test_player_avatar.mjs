import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const dataUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const threeUrl = dataUrl(await readFile(new URL('../../static/vendor/three/three.module.js', import.meta.url), 'utf8'));
const source = (await readFile(new URL('../../static/js/game/player-avatar.js', import.meta.url), 'utf8'))
  .replace("from 'three'", `from '${threeUrl}'`);
const THREE = await import(threeUrl);
const { createPlayerAvatar, playerRadius } = await import(dataUrl(source));
const refereeSource = (await readFile(new URL('../../static/js/game/referee-avatar.js', import.meta.url), 'utf8'))
  .replace("'./player-avatar.js?v=2'", `'${dataUrl(source)}'`);
const { createRefereeAvatar } = await import(dataUrl(refereeSource));
const { disposePlayerMeshes } = await import(dataUrl(await readFile(new URL('../../static/js/game/scene-resources.js', import.meta.url), 'utf8')));
// Canvas pixels are checked in the browser harness; this stand-in tests meshes.
const context = new Proxy({}, {
  get: (_, name) => name === 'createRadialGradient' ? () => ({ addColorStop() {} }) : () => {},
  set: () => true,
});
globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => context }) };

const player = (size = 50, number = 7, options = {}) =>
  createPlayerAvatar('#2563eb', false, { size }, number, { side: 'a', seed: 3, ...options });
function visibleMeshes(group) {
  const result = [];
  group.traverseVisible(child => { if (child.isMesh) result.push(child); });
  return result;
}
const triangles = geometry => (geometry.index?.count ?? geometry.attributes.position.count) / 3;

test('referee shares human geometry resources and has a separate official kit', () => {
  const a = createRefereeAvatar(), b = createRefereeAvatar();
  a.userData.moveTo(new THREE.Vector3()); b.userData.moveTo(new THREE.Vector3());
  assert.equal(visibleMeshes(a).length, 6);
  assert.equal(a.userData.avatar.body.geometry, b.userData.avatar.body.geometry);
  assert.equal(a.userData.avatar.kit.official, true);
  assert.ok(a.userData.top > 40 && a.userData.top < 46);
  assert.ok(!a.children.includes(a.userData.ring));
  const body = a.userData.avatar.body.geometry;
  assert.equal(a.userData.setShirtColor('#00aaee'), true);
  assert.notEqual(a.userData.avatar.body.geometry, body);
  assert.equal(b.userData.avatar.body.geometry, body);
  assert.equal(a.userData.setShirtColor('invalid'), false);
  assert.equal(a.userData.setShirtColor('#fde68a'), true);
  assert.equal(a.userData.avatar.body.geometry, body);
});

test('referee walks along exact server coordinates and rests on stop, teleport, or hide', () => {
  const ref = createRefereeAvatar();
  ref.userData.moveTo(new THREE.Vector3());
  const next = new THREE.Vector3(2, 0, 3);
  ref.userData.moveTo(next, 16);
  assert.deepEqual(ref.position.toArray(), next.toArray());
  assert.notEqual(ref.userData.avatar.pose.stride, 0);
  assert.equal(ref.userData.moveTo(next, 16), true, 'settling a moving pose needs a redraw');
  assert.equal(ref.userData.avatar.pose.stride, 0);
  assert.equal(ref.userData.moveTo(next, 16), false, 'unchanged snapshots stay idle');
  ref.userData.moveTo(new THREE.Vector3(200, 0, 300), 16);
  assert.equal(ref.userData.avatar.pose.stride, 0);
  ref.userData.moveTo(null);
  assert.equal(ref.visible, false);
});

test('referee whistle gesture ends, returns arm to rest, and never moves root position', () => {
  const ref = createRefereeAvatar();
  ref.userData.moveTo(new THREE.Vector3(12, 0, 24));
  ref.userData.signalWhistle(100);
  assert.equal(ref.userData.tick(300), true);
  assert.ok(ref.userData.avatar.arms[1].rotation.x < -2);
  assert.deepEqual(ref.position.toArray(), [12, 0, 24]);
  assert.equal(ref.userData.tick(1100), false);
  assert.equal(ref.userData.avatar.arms[1].rotation.x, 0);
  assert.equal(ref.userData.tick(1200), false);
});

test('legal zero Size stays zero, and player dimensions follow the physical radius', () => {
  for (const [size, radius] of [[0, 12], [50, 20], [100, 28]]) {
    const avatar = player(size);
    assert.equal(avatar.userData.radius, radius);
    assert.deepEqual(avatar.scale.toArray(), [1, 1, 1]);
    assert.equal(avatar.userData.avatar.athlete.scale.x, radius * .7);
    assert.ok(Math.abs(avatar.userData.top / radius - 2.282) < 1e-8);
    const bounds = new THREE.Box3().setFromObject(avatar);
    assert.ok(bounds.min.y >= -.001, 'boots should remain above the pitch');
    assert.ok(bounds.max.y <= avatar.userData.top + .1);
  }
  assert.equal(playerRadius({ size: -20 }), 12);
  assert.equal(playerRadius({ size: 140 }), 28);
  assert.equal(playerRadius({ size: NaN }), 20);
  assert.equal(playerRadius(undefined), 20);
  assert.equal(playerRadius({ size: 50 }, 22.5), 22.5);
});

test('each roster member has a stable natural appearance independent of reset order', () => {
  const before = Array.from({ length: 11 }, (_, seed) => player(50, seed + 1, { seed }).userData.avatar.appearance);
  const after = Array.from({ length: 11 }, (_, seed) => player(50, seed + 1, { seed }).userData.avatar.appearance);
  assert.deepEqual(before, after);
  assert.ok(new Set(before.map(look => look.skin)).size >= 5);
  assert.ok(new Set(before.map(look => look.hair)).size >= 4);
  assert.notDeepEqual(player(50, 7, { seed: 3, side: 'a' }).userData.avatar.appearance,
    player(50, 7, { seed: 3, side: 'b' }).userData.avatar.appearance);
});

test('rebuilding identical players reuses protected geometry, materials, and number textures', () => {
  const a = player(20), b = player(80);
  const meshesA = visibleMeshes(a), meshesB = visibleMeshes(b);
  assert.equal(meshesA.length, 7);
  assert.equal(meshesB.length, 7);
  for (let index = 0; index < meshesA.length; index++) {
    assert.equal(meshesA[index].geometry, meshesB[index].geometry);
    assert.equal(meshesA[index].material, meshesB[index].material);
    assert.equal(meshesA[index].geometry.userData.playerShared, true);
    assert.equal(meshesA[index].material.userData.playerShared, true);
    if (meshesA[index].material.map) assert.equal(meshesA[index].material.map.userData.playerShared, true);
  }
  assert.equal(a.getObjectByName('shirt-number').material.map.image.width, 128);
  assert.ok(meshesA.reduce((total, mesh) => total + triangles(mesh.geometry), 0) <= 1100);
  assert.equal(a.userData.avatar.legs[0].geometry, a.userData.avatar.legs[1].geometry);
  assert.equal(a.userData.avatar.arms[0].geometry, a.userData.avatar.arms[1].geometry);
  assert.equal(a.userData.avatar.body.geometry, player(50, 8).userData.avatar.body.geometry,
    'shirt number should not force body geometry duplication');
});

test('goalkeeper kit and gloves are distinct while stature and collision radius stay consistent', () => {
  const field = player();
  const keeper = createPlayerAvatar('#2563eb', true, { size: 50 }, 1, { side: 'a', seed: 3 });
  const custom = createPlayerAvatar('#2563eb', true, { size: 50 }, 1, { side: 'a', seed: 3, keeperColor: '#91396b' });
  assert.notEqual(keeper.userData.avatar.kit.jersey, field.userData.avatar.kit.jersey);
  assert.equal(custom.userData.avatar.kit.jersey, '#91396b');
  assert.notEqual(keeper.userData.avatar.arms[0].geometry, field.userData.avatar.arms[0].geometry);
  assert.equal(keeper.userData.radius, field.userData.radius);
  assert.equal(keeper.userData.top, field.userData.top);
  assert.ok(visibleMeshes(keeper).reduce((total, mesh) => total + triangles(mesh.geometry), 0) <= 1100);
});

test('selection identifies the physical footprint without changing the player scale', () => {
  const avatar = player(100);
  const original = avatar.scale.clone();
  assert.equal(avatar.userData.setSelected(true), true);
  assert.equal(avatar.userData.setSelected(true), false);
  assert.equal(avatar.userData.ring.visible, true);
  assert.equal(avatar.userData.ring.scale.x, 28);
  assert.ok(avatar.scale.equals(original));
  assert.equal(avatar.userData.setSelected(false), true);
  assert.equal(avatar.userData.ring.visible, false);
});

test('explicit motion poses articulate limbs then reset completely without moving the server position', () => {
  const avatar = player();
  avatar.position.set(123, 0, 456); avatar.rotation.y = .75;
  const position = avatar.position.clone(), rotation = avatar.quaternion.clone();
  assert.equal(avatar.userData.setPose({ stride: 1, kick: .5, lean: .2 }), true);
  assert.ok(Math.abs(avatar.userData.avatar.legs[1].rotation.x) > .5);
  assert.ok(avatar.position.equals(position));
  assert.ok(avatar.quaternion.equals(rotation));
  assert.equal(avatar.userData.setPose({ stride: 1, kick: .5, lean: .2 }), false);
  assert.equal(avatar.userData.setPose(), true);
  assert.deepEqual(avatar.userData.avatar.pose, { stride: 0, kick: 0, lean: 0 });
  assert.ok([...avatar.userData.avatar.legs, ...avatar.userData.avatar.arms].every(part => part.rotation.x === 0));
  assert.equal(avatar.userData.avatar.athlete.rotation.x, 0);
  assert.equal(avatar.userData.setPose({ stride: Infinity, kick: NaN }), false);
  avatar.userData.setPose({ stride: 999, kick: 999, lean: -999 });
  assert.deepEqual(avatar.userData.avatar.pose, { stride: 1, kick: 1, lean: -.25 });
});

test('removing a player releases private resources while keeping a teammate’s shared resources', () => {
  const scene = new THREE.Group(), first = player(), teammate = player();
  scene.add(first, teammate);
  const sharedGeometry = first.userData.avatar.body.geometry, sharedMaterial = first.userData.avatar.body.material;
  let sharedDisposals = 0, privateDisposals = 0;
  sharedGeometry.addEventListener('dispose', () => sharedDisposals++);
  sharedMaterial.addEventListener('dispose', () => sharedDisposals++);
  const geometry = new THREE.BoxGeometry(1,1,1), material = new THREE.MeshBasicMaterial();
  geometry.addEventListener('dispose', () => privateDisposals++);
  material.addEventListener('dispose', () => privateDisposals++);
  first.add(new THREE.Mesh(geometry, material));
  disposePlayerMeshes([first]);
  assert.equal(first.parent, null); assert.equal(teammate.parent, scene);
  assert.equal(privateDisposals, 2); assert.equal(sharedDisposals, 0);
  assert.equal(teammate.userData.avatar.body.geometry, sharedGeometry);
});
