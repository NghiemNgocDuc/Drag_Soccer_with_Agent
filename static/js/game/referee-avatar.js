import { createPlayerAvatar } from './player-avatar.js?v=2';

// Shares the player geometry/material cache. The group origin is the feet and
// follows authoritative referee coordinates; only the limbs are cosmetic.
export function createRefereeAvatar(shirtColor = '#fde68a') {
  const build = color => createPlayerAvatar(color, false, undefined, undefined,
    { role: 'referee', radius: 18.5, side: 'official', seed: 'match-referee' });
  const group = build(shirtColor);
  group.name = 'match-referee';
  group.remove(group.userData.ring);
  group.visible = false;
  const avatar = group.userData.avatar;
  let distance = 0, signalStart = null, signalAmount = 0;
  const wrap = angle => Math.atan2(Math.sin(angle), Math.cos(angle));

  function applySignal(amount) {
    // Raise a hand toward the whistle without moving the authoritative body.
    const arm = avatar.arms[1];
    arm.rotation.x = avatar.pose.stride * .32 * (1 - amount) - 2.3 * amount;
    arm.rotation.z = .12 - .77 * amount;
  }

  group.userData.moveTo = (position, dtMs = 0) => {
    const wasVisible = group.visible;
    if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.z)) {
      group.visible = false; group.userData.rest(); return wasVisible;
    }
    const dx = position.x - group.position.x, dz = position.z - group.position.z;
    const travel = Math.hypot(dx, dz);
    group.visible = true; group.position.copy(position);
    const walking = wasVisible && dtMs > 0 && dtMs <= 250 && travel > .005 && travel < 60;
    if (travel > .005) {
      const heading = Math.atan2(dx, dz);
      const rate = walking ? 1 - Math.exp(-12 * dtMs / 1000) : 1;
      group.rotation.y += wrap(heading - group.rotation.y) * rate;
    }
    let poseChanged = false;
    if (walking) {
      distance += travel;
      const speed = Math.min(1, travel / dtMs * 1000 / 100);
      group.userData.setPose({ stride: Math.sin(distance / 10) * .72 * speed, lean: .025 * speed });
      avatar.athlete.position.y = Math.abs(Math.sin(distance / 10)) * .025 * speed;
    } else poseChanged = group.userData.rest();
    if (signalAmount) applySignal(signalAmount);
    return travel > .005 || !wasVisible || walking || poseChanged;
  };
  group.userData.rest = () => {
    const changed = group.userData.setPose() || avatar.athlete.position.y !== 0;
    avatar.athlete.position.y = 0;
    if (signalAmount) applySignal(signalAmount);
    return changed;
  };
  group.userData.signalWhistle = now => { signalStart = now; };
  group.userData.tick = now => {
    if (signalStart === null) return false;
    const t = Math.max(0, now - signalStart);
    signalAmount = Math.min(1, t / 130) * Math.min(1, Math.max(0, (1000 - t) / 230));
    applySignal(signalAmount);
    if (t >= 1000) { signalStart = null; signalAmount = 0; group.userData.rest(); return false; }
    return true;
  };
  group.userData.setShirtColor = color => {
    if (typeof color !== 'string' || !/^#[0-9a-f]{6}$/i.test(color) || color.toLowerCase() === avatar.kit.jersey) return false;
    const replacement = build(color).userData.avatar;
    avatar.body.geometry = replacement.body.geometry;
    avatar.arms.forEach((arm, i) => { arm.geometry = replacement.arms[i].geometry; });
    avatar.kit = replacement.kit;
    return true;
  };
  return group;
}
