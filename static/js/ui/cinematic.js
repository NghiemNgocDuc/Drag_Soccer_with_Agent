/* Decorative page motion: CSS scenes + one frame per pointer event, never a frame loop. */
(() => {
  const root = document.documentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const precise = matchMedia('(hover: hover) and (pointer: fine)');
  const key = 'agent-soccer-motion';
  let preference = 'on';
  try { preference = localStorage.getItem(key) || 'on'; } catch (_) {}
  let enabled = false, frame = 0, pending = null;
  const entrances = new Set();
  const pointerBounds = new WeakMap();
  const depthTargets = [...document.querySelectorAll('[data-motion-depth]')];
  const cards = document.body.classList.contains('as-game') ? [] : [...document.querySelectorAll(
    '.as-destination,.workshop-link-card,.compete-link-card,.social-link-card'
  )];
  cards.forEach(card => card.setAttribute('data-motion-card', ''));
  const targets = [...depthTargets, ...cards];
  depthTargets.forEach(element => {
    const depth = Number(element.dataset.motionDepth);
    element.style.setProperty('--motion-depth', `${Number.isFinite(depth) ? Math.min(12, Math.max(0, depth)) : 8}px`);
  });

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'as-motion-toggle';
  toggle.setAttribute('data-motion-toggle', '');
  toggle.setAttribute('aria-label', 'Page motion');
  toggle.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path class="as-motion-pause" d="M5 3v10M11 3v10" stroke="currentColor" stroke-width="2"/><path class="as-motion-play" d="m5 3 8 5-8 5Z" fill="currentColor"/></svg><span>Motion on</span>';
  const controls = document.querySelector('.as-account,.as-login-nav-action') || document.querySelector('.as-nav');
  controls?.append(toggle);

  function resetPointer() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0; pending = null;
    targets.forEach(element => {
      pointerBounds.delete(element);
      element.style.removeProperty('--motion-x');
      element.style.removeProperty('--motion-y');
    });
  }
  function sync() {
    enabled = preference !== 'off' && !reduced.matches;
    root.classList.toggle('as-motion-enabled', enabled);
    root.classList.toggle('as-motion-paused', document.hidden || !enabled);
    toggle.setAttribute('aria-pressed', String(enabled));
    toggle.querySelector('span').textContent = enabled ? 'Motion on' : 'Motion off';
    toggle.title = reduced.matches ? 'Page motion follows your reduced-motion setting' : (enabled ? 'Pause page motion' : 'Enable page motion');
    toggle.disabled = reduced.matches;
    if (!enabled || document.hidden) {
      resetPointer();
      entrances.forEach(animation => animation.cancel());
      entrances.clear();
    }
  }
  toggle.addEventListener('click', () => {
    preference = enabled ? 'off' : 'on';
    try { localStorage.setItem(key, preference); } catch (_) {}
    sync();
  });
  reduced.addEventListener('change', sync);
  precise.addEventListener('change', resetPointer);
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('pagehide', resetPointer);
  window.addEventListener('pageshow', sync);
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) { preference = event.newValue || 'on'; sync(); }
  });
  sync();

  // Artwork runs only while visible. Gameplay canvases are deliberately not observed here.
  const scenes = document.querySelectorAll('[data-motion-scene]');
  if ('IntersectionObserver' in window) {
    const sceneObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => entry.target.classList.toggle('is-in-view', entry.isIntersecting));
    }, { threshold: 0 });
    scenes.forEach(scene => sceneObserver.observe(scene));
  }

  targets.forEach(element => {
    element.addEventListener('pointerenter', event => {
      if (event.pointerType !== 'mouse' || !enabled || !precise.matches) return;
      pointerBounds.set(element, element.getBoundingClientRect());
    }, { passive: true });
    element.addEventListener('pointermove', event => {
      if (!enabled || document.hidden || !precise.matches || event.pointerType !== 'mouse') return;
      const bounds = pointerBounds.get(element) || element.getBoundingClientRect();
      pointerBounds.set(element, bounds);
      pending = { element,
        x: Math.max(-1, Math.min(1, (event.clientX - bounds.left) / bounds.width * 2 - 1)),
        y: Math.max(-1, Math.min(1, (event.clientY - bounds.top) / bounds.height * 2 - 1)) };
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (!pending) return;
        pending.element.style.setProperty('--motion-x', pending.x.toFixed(3));
        pending.element.style.setProperty('--motion-y', pending.y.toFixed(3));
        pending = null;
      });
    }, { passive: true });
    element.addEventListener('pointerleave', () => {
      pointerBounds.delete(element);
      if (pending?.element === element) { cancelAnimationFrame(frame); frame = 0; pending = null; }
      element.style.removeProperty('--motion-x');
      element.style.removeProperty('--motion-y');
    }, { passive: true });
  });
  // Scrolling cancels a hover offset; it never schedules a decorative scroll loop.
  window.addEventListener('scroll', resetPointer, { passive: true });
  window.addEventListener('resize', resetPointer, { passive: true });

  if (document.body.classList.contains('as-game') || !('IntersectionObserver' in window)) return;
  const reveals = document.querySelectorAll(
    '.as-landing-hero>div:first-child>*,.as-page-header>div:first-child,' +
    '.as-login-story-copy,.as-login-heading,.as-auth-card>h1,' +
    '.as-destination,.workshop-link-card,.compete-link-card,.social-link-card,.as-section-top,.as-callout,.as-join-banner'
  );
  const revealObserver = new IntersectionObserver(entries => {
    let order = 0;
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      revealObserver.unobserve(entry.target);
      if (!enabled || document.hidden || entry.target.contains(document.activeElement) || !entry.target.animate) return;
      const animation = entry.target.animate([
        { opacity: 0.35, transform: 'translateY(14px)' },
        { opacity: 1, transform: 'translateY(0)' }
      ], { duration: 380, delay: Math.min(order++ * 40, 160), easing: 'cubic-bezier(.2,.8,.2,1)' });
      entrances.add(animation);
      const forget = () => entrances.delete(animation);
      animation.addEventListener('finish', forget, { once: true });
      animation.addEventListener('cancel', forget, { once: true });
    });
  }, { threshold: 0.08 });
  reveals.forEach(element => revealObserver.observe(element));
})();
