/* Achievements toast renderer — shared by the game, My Models and
 * tournament pages. The server drains pending badges into the
 * `achievements` array of existing JSON responses (no new polling);
 * pages call AW.handle(data) at their fetch sites. */
(function () {
  'use strict';

  var CSS_ID = 'ach-toast-css';
  if (!document.getElementById(CSS_ID)) {
    var style = document.createElement('style');
    style.id = CSS_ID;
    style.textContent = [
      '#ach-toasts{position:fixed;left:16px;bottom:16px;z-index:9999;display:flex;',
      'flex-direction:column;gap:8px;max-width:min(360px,92vw)}',
      '.ach-toast{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,.95);',
      'border:1px solid rgba(255,255,255,.7);border-radius:14px;padding:10px 14px;',
      'box-shadow:0 12px 40px rgba(2,6,23,.3);animation:ach-pop .25s ease-out;cursor:pointer}',
      '.ach-toast .ach-emoji{font-size:1.5rem;line-height:1}',
      '.ach-toast .ach-name{font-weight:700;font-size:.85rem;color:#0f172a}',
      '.ach-toast .ach-sub{font-size:.72rem;color:#64748b;margin-top:1px}',
      '.ach-toast .ach-unlock{font-size:.7rem;color:#0ea5e9;margin-top:3px}',
      '.ach-toast .ach-unlock b{font-weight:700}',
      '@keyframes ach-pop{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}',
      '@keyframes ach-fade{to{opacity:0;transform:translateY(6px)}}',
    ].join('');
    document.head.appendChild(style);
  }

  function showToast(ach) {
    if (!ach || !ach.name) return;
    var wrap = document.getElementById('ach-toasts');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'ach-toasts';
      document.body.appendChild(wrap);
    }
    var el = document.createElement('div');
    el.className = 'ach-toast';
    el.title = 'Achievement unlocked — view all on /achievements';
    var unlockLine = '';
    if (ach.unlock && ach.unlock.length) {
      unlockLine = '<div class="ach-unlock">Unlocked: ' +
        ach.unlock.map(function (u) { return '<b>' + esc(u) + '</b>'; }).join(', ') + '</div>';
    }
    el.innerHTML =
      '<span class="ach-emoji">' + (ach.emoji || '') + '</span>' +
      '<span><div class="ach-name">' + esc(ach.name) + '</div>' +
      '<div class="ach-sub">' + esc(ach.description || 'Achievement unlocked') + '</div>' +
      unlockLine + '</span>';
    el.addEventListener('click', function () { location.href = '/achievements'; });
    wrap.appendChild(el);
    while (wrap.children.length > 5) wrap.removeChild(wrap.firstChild);
    setTimeout(function () {
      el.style.animation = 'ach-fade .3s ease-in forwards';
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 320);
    }, 5200);
  }

  function esc(s) {
    if (s === null || s === undefined) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  window.AW = {
    handle: function (data) {
      var list = data && Array.isArray(data.achievements) ? data.achievements : null;
      if (!list || !list.length) return;
      list.forEach(showToast);
    },
    pump: function () {
      fetch('/api/achievements/toasts')
        .then(function (r) { return r.json(); })
        .then(function (data) { window.AW.handle(data); })
        .catch(function () { /* offline — toasts stay queued server-side */ });
    },
  };

  // Page-load pump: drain any toasts earned while away from the game.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { window.AW.pump(); });
  } else {
    window.AW.pump();
  }
})();
