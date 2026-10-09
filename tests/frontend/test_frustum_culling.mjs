import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

async function localModule(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8');
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}
const THREE = await localModule('../../static/vendor/three/three.module.js');
const { createInstancedFrustumCuller } = await localModule('../../static/js/game/frustum-culling.js');

function fixture(positions = [[0, 0, -50], [37, 0, -50], [150, 0, -50], [0, 0, 50]]) {
  const geometry = new THREE.PlaneGeometry(18, 28);
  const tags = new THREE.InstancedBufferAttribute(new Uint8Array(positions.flatMap((_, i) => [i, 255-i])), 2, true);
  geometry.setAttribute('aTag', tags);
  const mesh = new THREE.InstancedMesh(geometry, new THREE.MeshBasicMaterial(), positions.length);
  positions.forEach((position, index) => {
    mesh.setMatrixAt(index, new THREE.Matrix4().makeTranslation(...position));
    mesh.setColorAt(index, new THREE.Color(index / positions.length, 0.5, 1));
  });
  const camera = new THREE.PerspectiveCamera(60, 1, 1, 200);
  camera.lookAt(0, 0, -1);
  const originalMatrices = mesh.instanceMatrix.array.slice();
  const originalColors = mesh.instanceColor?.array.slice() || new Float32Array();
  const culler = createInstancedFrustumCuller(THREE, mesh, { padding: 8 });
  return { camera, mesh, tags, culler, originalMatrices, originalColors };
}

test('classifies front, behind, outside, and partially visible edge billboards conservatively', () => {
  const f = fixture();
  assert.equal(f.culler.update(f.camera), 2);
  assert.deepEqual(f.culler.visibleIndices(), [0, 1]);
  // The edge fan center is outside, but its left edge is in the frame.
  assert.ok(37 > Math.tan(Math.PI / 6) * 50);
  assert.ok(37 - 9 < Math.tan(Math.PI / 6) * 50);
  assert.equal(f.mesh.frustumCulled, true);
});

test('camera reversal compacts matrices, colors, and custom attributes together, then restores originals', () => {
  const f = fixture();
  f.culler.update(f.camera);
  f.camera.lookAt(0, 0, 1);
  assert.equal(f.culler.update(f.camera), 1);
  assert.deepEqual(f.culler.visibleIndices(), [3]);
  assert.deepEqual(Array.from(f.mesh.instanceMatrix.array.slice(0, 16)), Array.from(f.originalMatrices.slice(48, 64)));
  assert.deepEqual(Array.from(f.mesh.instanceColor.array.slice(0, 3)), Array.from(f.originalColors.slice(9, 12)));
  assert.deepEqual(Array.from(f.tags.array.slice(0, 2)), [3, 252]);
  f.camera.lookAt(0, 0, -1);
  f.culler.update(f.camera);
  assert.deepEqual(f.culler.visibleIndices(), [0, 1]);
  assert.deepEqual(Array.from(f.mesh.instanceMatrix.array.slice(0, 32)), Array.from(f.originalMatrices.slice(0, 32)));
  assert.deepEqual(Array.from(f.tags.array.slice(0, 4)), [0, 255, 1, 254]);
  assert.equal(f.tags.normalized, true);
});

test('unchanged camera and mesh leave GPU attribute versions and selection updates unchanged', () => {
  const f = fixture();
  f.culler.update(f.camera);
  const before = [f.mesh.instanceMatrix.version, f.mesh.instanceColor.version, f.tags.version, f.culler.stats().updates];
  for (let index = 0; index < 5; index++) f.culler.update(f.camera);
  assert.deepEqual([f.mesh.instanceMatrix.version, f.mesh.instanceColor.version, f.tags.version, f.culler.stats().updates], before);
});

test('empty selection submits zero instances and returning the camera restores every visible source', () => {
  const f = fixture();
  f.culler.update(f.camera);
  f.camera.lookAt(0, 1000, 0);
  assert.equal(f.culler.update(f.camera), 0);
  assert.deepEqual(f.culler.visibleIndices(), []);
  assert.equal(f.culler.stats().total, 4);
  f.camera.lookAt(0, 0, -1);
  f.culler.update(f.camera);
  assert.deepEqual(f.culler.visibleIndices(), [0, 1]);
});

test('parent movement and nonuniform scale are included in world-space sphere classification', () => {
  const f = fixture([[0, 0, -50]]);
  const parent = new THREE.Group();
  parent.add(f.mesh);
  f.culler.update(f.camera);
  assert.equal(f.mesh.count, 1);
  parent.position.x = 200;
  assert.equal(f.culler.update(f.camera), 0);
  parent.position.x = 0;
  parent.scale.set(2, 1, 1);
  assert.equal(f.culler.update(f.camera), 1);
});

test('native whole-mesh bound remains conservative after the compact selection changes', () => {
  const f = fixture();
  const originalBound = f.mesh.boundingSphere.clone();
  f.culler.update(f.camera);
  f.camera.lookAt(0, 0, 1);
  f.culler.update(f.camera);
  assert.ok(f.mesh.boundingSphere.equals(originalBound));
  const frustum = new THREE.Frustum().setFromProjectionMatrix(
    new THREE.Matrix4().multiplyMatrices(f.camera.projectionMatrix, f.camera.matrixWorldInverse));
  assert.ok(frustum.intersectsObject(f.mesh));
  for (const point of [[0, 0, -50], [150, 0, -50], [0, 0, 50]]) {
    assert.ok(originalBound.containsPoint(new THREE.Vector3(...point)));
  }
});

test('visible index diagnostics cannot mutate the source selection', () => {
  const f = fixture();
  f.culler.update(f.camera);
  f.culler.visibleIndices().push(100);
  assert.deepEqual(f.culler.visibleIndices(), [0, 1]);
});

test('empty audiences are safe and shared-per-instance attributes are rejected explicitly', () => {
  const f = fixture([]);
  assert.equal(f.culler.update(f.camera), 0);
  assert.deepEqual(f.culler.stats(), { total: 0, visible: 0, updates: 0 });
  const mesh = new THREE.InstancedMesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial(), 2);
  mesh.geometry.setAttribute('aGrouped', new THREE.InstancedBufferAttribute(new Float32Array(1), 1, false, 2));
  assert.throws(() => createInstancedFrustumCuller(THREE, mesh), /one attribute value per instance/);
});
