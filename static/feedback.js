(function () {
  var FEEDBACK_HTML =
    '<div id="fb-fab" title="Send feedback" style="position:fixed;bottom:24px;right:24px;z-index:9999;width:52px;height:52px;border-radius:50%;background:linear-gradient(135deg,#06b6d4,#8b5cf6);color:#fff;border:none;cursor:pointer;box-shadow:0 6px 24px rgba(6,182,212,.4);display:flex;align-items:center;justify-content:center;font-size:1.4rem;transition:transform .2s,box-shadow .2s">' +
      '<span style="transform:translateY(-1px)">&#9993;</span>' +
    '</div>' +
    '<div id="fb-overlay" style="display:none;position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,.35);backdrop-filter:blur(4px);align-items:center;justify-content:center;padding:16px">' +
      '<div style="background:rgba(255,255,255,.85);backdrop-filter:blur(18px);border:1px solid rgba(255,255,255,.5);border-radius:18px;box-shadow:0 24px 60px rgba(80,120,200,.15);padding:28px 24px;width:100%;max-width:460px;position:relative;font-family:\'Inter\',system-ui,sans-serif">' +
        '<button id="fb-close" style="position:absolute;top:12px;right:14px;background:none;border:none;font-size:1.3rem;cursor:pointer;color:#94a3b8;line-height:1" title="Close">&times;</button>' +
        '<h3 style="font-size:1.1rem;font-weight:700;color:#0f172a;margin:0 0 4px">Send Feedback</h3>' +
        '<p style="font-size:.78rem;color:#475569;margin:0 0 18px">Help us improve Agent Soccer</p>' +
        '<div id="fb-msg" style="display:none;font-size:.8rem;padding:10px 14px;border-radius:8px;margin-bottom:14px"></div>' +
        '<div class="fb-field" style="margin-bottom:14px">' +
          '<label style="display:block;font-size:.75rem;font-weight:500;color:#475569;margin-bottom:5px">Title *</label>' +
          '<input id="fb-title" type="text" placeholder="Brief summary of your feedback" style="width:100%;padding:10px 13px;background:rgba(255,255,255,.7);border:1px solid rgba(255,255,255,.5);border-radius:9px;font-family:inherit;font-size:.88rem;color:#0f172a;outline:none;transition:border-color .15s" autofocus>' +
        '</div>' +
        '<div class="fb-field" style="margin-bottom:18px">' +
          '<label style="display:block;font-size:.75rem;font-weight:500;color:#475569;margin-bottom:5px">Description</label>' +
          '<textarea id="fb-desc" placeholder="Tell us more..." rows="4" style="width:100%;padding:10px 13px;background:rgba(255,255,255,.7);border:1px solid rgba(255,255,255,.5);border-radius:9px;font-family:inherit;font-size:.88rem;color:#0f172a;outline:none;resize:vertical;transition:border-color .15s"></textarea>' +
        '</div>' +
        '<button id="fb-submit" style="width:100%;padding:11px;background:linear-gradient(135deg,#06b6d4,#0891b2);color:#fff;font-family:inherit;font-size:.88rem;font-weight:600;border:none;border-radius:9px;cursor:pointer;box-shadow:0 4px 16px rgba(6,182,212,.35);transition:opacity .15s,transform .12s">Send Feedback</button>' +
      '</div>' +
    '</div>';

  var inserted = document.createElement('div');
  inserted.innerHTML = FEEDBACK_HTML;
  document.body.appendChild(inserted);

  var fab = document.getElementById('fb-fab');
  var overlay = document.getElementById('fb-overlay');
  var close = document.getElementById('fb-close');
  var title = document.getElementById('fb-title');
  var desc = document.getElementById('fb-desc');
  var submit = document.getElementById('fb-submit');
  var msg = document.getElementById('fb-msg');

  fab.addEventListener('click', function () {
    overlay.style.display = 'flex';
    title.focus();
    msg.style.display = 'none';
    msg.className = '';
  });

  function closeModal() {
    overlay.style.display = 'none';
    title.value = '';
    desc.value = '';
  }

  close.addEventListener('click', closeModal);
  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) closeModal();
  });

  submit.addEventListener('click', function () {
    var t = title.value.trim();
    if (!t) {
      showMsg('Please enter a title.', 'error');
      return;
    }
    submit.disabled = true;
    submit.textContent = 'Sending...';

    fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: t, description: desc.value.trim() }),
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.ok) {
        showMsg('Thanks for your feedback!', 'success');
        title.value = '';
        desc.value = '';
        setTimeout(closeModal, 1400);
      } else {
        showMsg(data.error || 'Failed to send feedback.', 'error');
        submit.disabled = false;
        submit.textContent = 'Send Feedback';
      }
    })
    .catch(function () {
      showMsg('Network error. Try again.', 'error');
      submit.disabled = false;
      submit.textContent = 'Send Feedback';
    });
  });

  function showMsg(text, type) {
    msg.style.display = 'block';
    msg.textContent = text;
    msg.style.background = type === 'success' ? 'rgba(16,185,129,.12)' : 'rgba(239,68,68,.12)';
    msg.style.border = type === 'success' ? '1px solid rgba(16,185,129,.3)' : '1px solid rgba(239,68,68,.3)';
    msg.style.color = type === 'success' ? '#059669' : '#dc2626';
  }

  fab.addEventListener('mouseenter', function () {
    fab.style.transform = 'scale(1.1)';
    fab.style.boxShadow = '0 8px 32px rgba(6,182,212,.5)';
  });
  fab.addEventListener('mouseleave', function () {
    fab.style.transform = 'scale(1)';
    fab.style.boxShadow = '0 6px 24px rgba(6,182,212,.4)';
  });
})();
