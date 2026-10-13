// Keeper gestures choose a save; they never move the authoritative physics body.
export function goalkeeperAccess(state, online = {}, busy = false) {
  const canSave = !!state && state.game_mode === 'hvai' && state.penalty_shootout
    && state.is_player_a === false && !state.game_over && !busy && !online.active
    && !state.penalty_goalkeeper_move && !!state.players_a?.[0] && !!state.ball;
  return { team: 'a', indices: canSave ? [0] : [], canSave, useBall: false };
}

// Server directions are pitch-relative: left is negative Y, right is positive Y.
// Drag toward the ball to hold the centre; a backward drag cancels the choice.
export function keeperDirectionFromDrag(origin, point, ball, minDistance = 8) {
  if (![origin?.x, origin?.y, point?.x, point?.y, ball?.x, ball?.y].every(Number.isFinite)) return null;
  const dx = point.x - origin.x, dy = point.y - origin.y;
  const length = Math.hypot(ball.x - origin.x, ball.y - origin.y);
  if (Math.hypot(dx, dy) < Math.max(0, minDistance) || !length || (!dx && !dy)) return null;
  const fx = (ball.x - origin.x) / length, fy = (ball.y - origin.y) / length;
  const forward = dx * fx + dy * fy;
  const lateral = (-dx * fy + dy * fx) * (fx < 0 ? -1 : 1);
  if (forward > 0 && forward >= Math.abs(lateral)) return 'center';
  if (Math.abs(lateral) > Math.abs(forward)) return lateral < 0 ? 'left' : 'right';
  return null;
}

export function createGoalkeeperInput(options) {
  const { canvas, getState, getGround, hitTest, onSubmit } = options;
  const doc = canvas.ownerDocument ?? document, win = doc.defaultView ?? window;
  const online = () => options.getOnline?.() ?? {};
  const access = () => goalkeeperAccess(getState(), online(), !!options.isBusy?.());
  const stamp = () => {
    const state = getState(), room = online();
    return [room.active, room.roomId, room.generation, state?.game_mode, state?.penalty_shootout,
      state?.is_player_a, state?.game_over, state?.kick_count, state?.penalty_kick_num,
      state?.penalty_goalkeeper_move, state?.penalty_a_score, state?.penalty_b_score,
      state?.penalty_kicks?.length].join(':');
  };
  let gesture = null, committed = null, pending = false, destroyed = false;
  const cleanups = [];
  const listen = (target, name, fn, capture = false) => {
    target.addEventListener(name, fn, capture);
    cleanups.push(() => target.removeEventListener(name, fn, capture));
  };
  const available = () => !destroyed && !pending && committed !== stamp() && access().canSave;
  function cancel() {
    const previous = gesture;
    gesture = null;
    if (previous) {
      try { if (canvas.hasPointerCapture(previous.id)) canvas.releasePointerCapture(previous.id); } catch (_) {}
      options.onClear?.();
    }
  }
  function sync() {
    if (gesture && (gesture.stamp !== stamp() || !available())) cancel();
  }
  function update(event) {
    if (!gesture || event.pointerId !== gesture.id) return;
    sync();
    if (!gesture) return;
    const ground = getGround(event.clientX, event.clientY);
    if (!ground) { gesture.direction = null; options.onClear?.(); return; }
    const point = { x: gesture.origin.x + ground.x - gesture.anchor.x,
      y: gesture.origin.y + ground.y - gesture.anchor.y };
    gesture.direction = Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) >= 8
      ? keeperDirectionFromDrag(gesture.origin, point, gesture.ball, 0) : null;
    options.onPreview?.(gesture.direction, { origin: gesture.origin, point });
  }
  const consume = event => { event.preventDefault(); event.stopImmediatePropagation(); };
  listen(canvas, 'pointerdown', event => {
    if (gesture) {
      if (event.pointerId !== gesture.id) cancel();
      consume(event); return;
    }
    if (event.button !== 0 || event.isPrimary === false || !available()) return;
    const legal = access();
    if (hitTest(event.clientX, event.clientY, legal) !== 0) return;
    const anchor = getGround(event.clientX, event.clientY), state = getState();
    if (!anchor) return;
    const keeper = state.players_a[0];
    gesture = { id: event.pointerId, x: event.clientX, y: event.clientY, anchor,
      origin: { x: keeper.x, y: keeper.y }, ball: { x: state.ball.x, y: state.ball.y },
      stamp: stamp(), direction: null };
    canvas.focus({ preventScroll: true });
    try { canvas.setPointerCapture(event.pointerId); } catch (_) {}
    consume(event);
  }, true);
  listen(canvas, 'pointermove', event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    update(event); consume(event);
  }, true);
  listen(canvas, 'pointerup', event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    update(event);
    const direction = gesture?.direction, token = gesture?.stamp;
    const submit = direction && token === stamp() && available();
    cancel(); consume(event);
    if (!submit) return;
    committed = token; pending = true;
    Promise.resolve().then(() => {
      if (!destroyed && stamp() === token && access().canSave) return onSubmit(direction);
      if (committed === token) committed = null;
    }).catch(error => {
      if (committed === token) committed = null;
      if (!destroyed) options.onError?.(error);
    }).finally(() => { pending = false; });
  }, true);
  for (const name of ['pointercancel', 'lostpointercapture']) listen(canvas, name, event => {
    if (gesture?.id === event.pointerId) cancel();
  });
  listen(doc, 'keydown', event => {
    if (event.key === 'Escape' && gesture) { cancel(); event.preventDefault(); }
  });
  listen(win, 'blur', cancel);
  listen(doc, 'visibilitychange', () => { if (doc.hidden) cancel(); });
  return { sync, cancel,
    destroy() { cancel(); destroyed = true; cleanups.forEach(remove => remove()); },
    snapshot: () => ({ canSave: available(), dragging: !!gesture,
      direction: gesture?.direction ?? null, pending }) };
}
