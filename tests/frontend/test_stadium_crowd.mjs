import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const dataUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const threeUrl = dataUrl(await readFile(new URL('../../static/vendor/three/three.module.js', import.meta.url), 'utf8'));
const cullingUrl = dataUrl(await readFile(new URL('../../static/js/game/frustum-culling.js', import.meta.url), 'utf8'));
const source = (await readFile(new URL('../../static/js/game/stadium-crowd.js', import.meta.url), 'utf8'))
  .replace("from 'three'", `from '${threeUrl}'`)
  .replace("from './frustum-culling.js'", `from '${cullingUrl}'`);
const THREE = await import(threeUrl);
const { audienceLayout, createCrowdSprites, createStadiumAudience } = await import(dataUrl(source));
// Headless canvas stand-in is only for construction; real pixels are browser-verified.
const context = new Proxy({}, { get: (_, name) => name === 'createLinearGradient' ? () => ({ addColorStop() {} }) : () => {}, set: () => true });
globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => context }) };

function fixture() {
  let invalidations = 0;
  const audience = createStadiumAudience(1400, 875, { detailed: false, invalidate: () => invalidations++ });
  return { audience, invalidations: () => invalidations };
}

test('audience layout has repeatable aisles, vacant seats, and a tier under each fan', () => {
  const layout = audienceLayout(1400, 875);
  assert.deepEqual(layout, audienceLayout(1400, 875));
  assert.equal(layout.tiers.length, 32);
  assert.ok(layout.positions.length > 2000 && layout.positions.length < 2400);
  const occupancy = layout.positions.length / layout.seats.length;
  assert.ok(occupancy > .92 && occupancy < .98);
  const fanSeats = new Set(layout.seats.map(seat => seat.position.join(',')));
  layout.positions.forEach(position => assert.ok(fanSeats.has(position.join(','))));
  for (let side = 0; side < 4; side++) {
    const row = layout.seats.filter(seat => seat.side === side && seat.row === 0);
    const along = row.map(seat => seat.position[side < 2 ? 0 : 2]);
    assert.ok(along.some((value, index) => index > 0 && value - along[index - 1] === 42));
    assert.ok(row.every(seat => Math.abs(seat.position[1] - 3.9) < 1e-8));
  }
});

test('crowd and all seating use four instanced draws and three shared geometries', () => {
  const { audience } = fixture();
  assert.equal(audience.group.children.length, 4);
  assert.ok(audience.group.children.every(mesh => mesh.isInstancedMesh));
  assert.equal(new Set(audience.group.children.map(mesh => mesh.geometry)).size, 3);
  assert.equal(audience.seating[1].geometry.index.count, 6);
  assert.equal(audience.seating[1].material.side, THREE.DoubleSide);
  assert.equal(audience.mesh.geometry.index.count, 6);
  assert.equal(audience.mesh.material.depthWrite, true);
  assert.equal(audience.mesh.material.transparent, false);
  assert.equal(audience.stats().variants, 8);
  assert.equal(audience.stats().poses, 2);
});

test('palette and seat customization change uniforms without rebuilding fan buffers', () => {
  const { audience, invalidations } = fixture();
  const before = audience.mesh.instanceMatrix.array.slice();
  const spriteVersions = Object.values(audience.mesh.geometry.attributes).map(attribute => attribute.version);
  const uniforms = audience.uniforms;
  audience.setPalette({ paletteName: 'team_a', teamA: '#17745b', teamB: '#9a335a' });
  assert.equal(uniforms.uPaletteMode.value, 2);
  assert.equal(uniforms.uTeamA.value.getHexString(), '17745b');
  audience.setPalette({ paletteName: 'team_b', teamA: '#17745b', teamB: '#9a335a' });
  assert.equal(uniforms.uPaletteMode.value, 3);
  audience.setPalette({ paletteName: 'mono' });
  assert.equal(uniforms.uPaletteMode.value, 1);
  assert.equal(uniforms.uPaletteCount.value, 4);
  audience.setSeatColor('#916337');
  assert.ok(audience.seating.every(mesh => mesh.material.color.getHexString() === '916337'));
  assert.deepEqual(audience.mesh.instanceMatrix.array, before);
  assert.deepEqual(Object.values(audience.mesh.geometry.attributes).map(attribute => attribute.version), spriteVersions);
  assert.equal(invalidations(), 5);
});

test('foot-centered bounds include cheering and overhead billboards at camera edges', () => {
  const crowd = createCrowdSprites([[0, 0, -50]], { detailed: false });
  const bound = crowd.mesh.geometry.boundingSphere;
  assert.deepEqual(bound.center.toArray(), [0, 0, 0]);
  for (const tilt of [0, .3, .7, Math.PI / 2]) {
    const up = new THREE.Vector3(0, Math.cos(tilt), Math.sin(tilt));
    for (const x of [-13, 13]) for (const y of [0, 38]) {
      const point = up.clone().multiplyScalar(y).add(new THREE.Vector3(x, .9, 0));
      assert.ok(bound.containsPoint(point));
    }
  }
  const camera = new THREE.PerspectiveCamera(60, 1, 1, 200);
  camera.lookAt(0, 0, -1);
  assert.equal(crowd.culler.update(camera), 1);
});

test('looking away culls the audience and seat instances, and return restores exact selections', () => {
  const { audience } = fixture();
  const camera = new THREE.PerspectiveCamera(65, 1.6, 1, 6000);
  camera.position.set(-1500, 790, 320); camera.lookAt(0, 0, 0);
  audience.update(camera);
  const meshes = [audience.mesh, ...audience.seating];
  const selected = meshes.map(mesh => mesh.userData.frustumCuller.visibleIndices());
  assert.ok(selected.every(indices => indices.length > 0));
  camera.lookAt(camera.position.clone().multiplyScalar(2)); audience.update(camera);
  assert.ok(meshes.every(mesh => mesh.count === 0));
  camera.lookAt(0, 0, 0); audience.update(camera);
  assert.deepEqual(meshes.map(mesh => mesh.userData.frustumCuller.visibleIndices()), selected);
});
