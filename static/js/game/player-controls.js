// Pure command rules shared by the HUD, pointer aim and keyboard controls.
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
export const normalizeAngle = angle => ((Number(angle) % 360) + 360) % 360;

export function controlAccess(state, online = {}, busy = false) {
  const side = online.active ? online.side : state?.game_mode === 'hvh' && !state.is_player_a ? 'b' : 'a';
  const blocked = reason => ({ team: side, indices: [], canKick: false, useBall: false, reason });
  if (!state) return blocked('Loading match…');
  if (state.game_over) return blocked('Match finished');
  if (busy) return blocked('Move in progress');
  if (online.active) {
    if (!['a', 'b'].includes(side)) return blocked('Watching this match');
    if (online.leaving) return blocked('Leaving match…');
    if (online.connectionError) return blocked(online.connectionError);
    if (online.ready === false) return blocked('Loading online match…');
    if (online.status === 'waiting') return blocked('Waiting for your opponent');
    if (!!state.is_player_a !== (side === 'a')) return blocked('Opponent’s turn');
  } else {
    if (state.game_mode === 'aivai') return blocked('AI controls both teams');
    if (state.game_mode !== 'hvh' && !state.is_player_a) return blocked(state.penalty_shootout ? 'Choose your goalkeeper direction' : 'AI’s turn');
  }
  const players = state[side === 'a' ? 'players_a' : 'players_b'] || [];
  if (!players.length) return blocked('No player available');
  const captainOnly = !online.active && state.game_mode !== 'hvh';
  const indices = captainOnly || state.penalty_shootout ? [0] : players.map((_, index) => index);
  return { team: side, indices, canKick: true, useBall: !!state.penalty_shootout,
    reason: state.penalty_shootout ? `Team ${side.toUpperCase()} · Penalty kick` : captainOnly ? 'Team A · Captain control' : `Team ${side.toUpperCase()} · Select a player` };
}

// Online turns have their own monotonic revision because regulation kick_count
// does not advance during shootouts. Older room payloads keep working.
export function onlineRevision(game, response = {}) {
  const revision = Number(response.move_count ?? game?.online_move_count ?? game?.kick_count ?? 0);
  return Number.isSafeInteger(revision) && revision >= 0 ? revision : 0;
}

export function onlineReplayPlan(response, sinceRevision) {
  const revision = onlineRevision(response.game, response);
  if (!Number.isSafeInteger(sinceRevision) || sinceRevision < 0) return { revision, moves: [], snap: true };
  if (revision <= sinceRevision) return { revision, moves: [], snap: revision < sinceRevision };
  const rawMoves = Array.isArray(response.moves) && response.moves.length ? response.moves
    : response.last_move ? [{ ...response.last_move, kick_count: response.last_move.kick_count ?? revision }] : [];
  const unique = new Map();
  for (const move of rawMoves) {
    const count = Number(move.kick_count);
    if (Number.isSafeInteger(count) && count > sinceRevision && count <= revision) unique.set(count, move);
  }
  const moves = [...unique.entries()].sort((a, b) => a[0] - b[0]);
  const complete = moves.length === revision - sinceRevision && moves.every(([count], index) => count === sinceRevision + index + 1);
  return { revision, moves: complete ? moves.map(([, move]) => move) : [], snap: !complete };
}

export function playerRadius(player) {
  const size = Number(player?.stats?.size ?? 50);
  return 12 + clamp(Number.isFinite(size) ? size : 50, 0, 100) * .16;
}

export function shotPower(basePower, passType = 'normal', powerCap = 100) {
  const strength = Number(basePower), cap = Number(powerCap);
  const multiplier = { normal: 1, short: .62, long: 1.08, through: .88 }[passType] ?? 1;
  return clamp((Number.isFinite(strength) ? strength : 0) * multiplier, 0, Math.min(100, Number.isFinite(cap) ? Math.max(0, cap) : 100));
}

// A gesture keeps its origin fixed. Screen movement is measured separately so
// touching a tall player's head cannot count as a pull from their feet.
export function shotFromPull(origin, point, maxDrag = 110, passType = 'normal', powerCap = 100) {
  const dx = point.x - origin.x, dy = point.y - origin.y;
  const distance = Math.hypot(dx, dy), fraction = distance ? Math.min(maxDrag, distance) / distance : 0;
  const basePower = clamp(distance / maxDrag * 100, 0, 100);
  return { pullX: origin.x + dx * fraction, pullY: origin.y + dy * fraction,
    angle: normalizeAngle(Math.atan2(-dy, -dx) * 180 / Math.PI), basePower,
    power: shotPower(basePower, passType, powerCap) };
}
