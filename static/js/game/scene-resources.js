// Rebuilt player groups own their private resources. Cached avatar resources
// remain available to the other team, bench and subsequent matches.
export function disposePlayerMeshes(meshes) {
  const geometries = new Set(), materials = new Set();
  for (const mesh of meshes) {
    mesh.removeFromParent();
    mesh.traverse(child => {
      if (child.geometry) geometries.add(child.geometry);
      if (child.material) {
        for (const material of Array.isArray(child.material) ? child.material : [child.material]) {
          materials.add(material);
        }
      }
    });
  }
  for (const geometry of geometries) if (!geometry.userData.playerShared) geometry.dispose();
  for (const material of materials) if (!material.userData.playerShared) material.dispose();
}
