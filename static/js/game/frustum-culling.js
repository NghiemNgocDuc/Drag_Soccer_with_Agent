// Static audience instances keep their original data separate from the compact
// GPU buffers, so turning the camera never loses fans or their sprite/color.
export function createInstancedFrustumCuller(THREE, mesh, options = {}) {
  const total = mesh.count;
  const padding = Math.max(0, Number(options.padding) || 0);
  const geometry = mesh.geometry;
  if (!geometry.boundingSphere) geometry.computeBoundingSphere();

  const attributes = [mesh.instanceMatrix];
  if (mesh.instanceColor) attributes.push(mesh.instanceColor);
  for (const attribute of Object.values(geometry.attributes)) {
    if (!attribute.isInstancedBufferAttribute) continue;
    if (attribute.meshPerAttribute !== 1) {
      throw new Error('Audience culling requires one attribute value per instance.');
    }
    attributes.push(attribute);
  }
  const buffers = attributes.map(attribute => ({
    attribute, original: attribute.array.slice(0, total * attribute.itemSize),
  }));
  const originals = buffers[0].original;
  const centers = new Float64Array(total * 3);
  const radii = new Float64Array(total);
  const matrix = new THREE.Matrix4();
  const sphere = new THREE.Sphere();
  const wholeBounds = new THREE.Sphere().makeEmpty();
  for (let index = 0; index < total; index++) {
    matrix.fromArray(originals, index * 16);
    sphere.copy(geometry.boundingSphere).applyMatrix4(matrix);
    sphere.radius += padding;
    sphere.center.toArray(centers, index * 3);
    radii[index] = sphere.radius;
    wholeBounds.union(sphere);
  }
  // Native whole-object culling remains safe for shader billboards because the
  // sphere covers every original fan, including its maximum animated offset.
  mesh.boundingSphere = wholeBounds;
  mesh.frustumCulled = true;

  const worldCenters = new Float64Array(total * 3);
  const worldRadii = new Float64Array(total);
  const clipMatrix = new THREE.Matrix4();
  const frustum = new THREE.Frustum();
  const previousClip = new Float64Array(16).fill(NaN);
  const previousWorld = new Float64Array(16).fill(NaN);
  let visibleIndices = Array.from({ length: total }, (_, index) => index);
  let updates = 0;

  function changed(elements, previous) {
    return elements.some((value, index) => value !== previous[index]);
  }

  function update(camera) {
    camera.updateWorldMatrix(true, false);
    mesh.updateWorldMatrix(true, false);
    clipMatrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    const worldChanged = changed(mesh.matrixWorld.elements, previousWorld);
    if (!worldChanged && !changed(clipMatrix.elements, previousClip)) return mesh.count;
    previousClip.set(clipMatrix.elements);
    if (worldChanged) {
      previousWorld.set(mesh.matrixWorld.elements);
      const worldScale = mesh.matrixWorld.getMaxScaleOnAxis();
      for (let index = 0; index < total; index++) {
        sphere.center.fromArray(centers, index * 3).applyMatrix4(mesh.matrixWorld);
        sphere.center.toArray(worldCenters, index * 3);
        worldRadii[index] = radii[index] * worldScale;
      }
    }
    frustum.setFromProjectionMatrix(clipMatrix);
    const next = [];
    for (let index = 0; index < total; index++) {
      sphere.center.fromArray(worldCenters, index * 3);
      sphere.radius = worldRadii[index];
      if (frustum.intersectsSphere(sphere)) next.push(index);
    }
    const selectionChanged = next.length !== visibleIndices.length ||
      next.some((index, slot) => index !== visibleIndices[slot]);
    if (selectionChanged) {
      for (const { attribute, original } of buffers) {
        const size = attribute.itemSize;
        for (let slot = 0; slot < next.length; slot++) {
          const start = next[slot] * size;
          const destination = slot * size;
          for (let component = 0; component < size; component++) {
            attribute.array[destination + component] = original[start + component];
          }
        }
        if (next.length > 0) attribute.needsUpdate = true;
      }
      visibleIndices = next;
      updates++;
    }
    mesh.count = next.length;
    return mesh.count;
  }

  const culler = {
    update,
    stats() { return { total, visible: mesh.count, updates }; },
    visibleIndices() { return visibleIndices.slice(); },
  };
  mesh.userData.frustumCuller = culler;
  return culler;
}
