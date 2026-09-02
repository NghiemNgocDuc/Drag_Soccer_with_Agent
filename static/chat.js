/* static/chat.js — shared chat component used in three surfaces:
   in-match chat (index_3d), tournament lobby (tournament_view), friend DMs
   (messages.html). Vanilla JS, no build step.

   Delivery: polling GET /chat/messages?scope&scope_id&after=<mid> — the same
   diff-cursor pattern the room-state endpoints use. Send via POST /chat/send
   (server-side rate limit + profanity filter). Report + block per message.

   Emoji: native Unicode input + a curated picker grid; in-match chat also
   gets a quick-reactions row (emoji-only one-tap sends).
*/
(function () {
  'use strict';

  var EMOJI_GRID = [
    '⚽','🔥','😂','😍','🥰','😎','🤩','😭',
    '😡','🥳','🤔','🙏','💪','👏','❤️','💙',
    '💚','💛','💜','🖤','🏆','🥅','👟','🎯',
    '🚀','⭐','🌟','✨','💯','🎉','🎊','👑',
    '💀','👻','🤖','👾','🎮','😅','😆','🤣',
    '😇','🥺','😤','🤯','🥶','🤠','👊','🤝'
  ];
  var QUICK_REACTIONS = ['⚽','🔥','👏','💯','😂','❤️','🎉','🏆'];
  var GIF_GRID = [
    'https://media.giphy.com/media/l0HlN2wYV4b64BwaI/giphy.gif',
    'https://media.giphy.com/media/3o7TKMt1VVNkHV2PaE/giphy.gif',
    'https://media.giphy.com/media/xT9IgG50Fb7Mi0prBC/giphy.gif',
    'https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif',
    'https://media.giphy.com/media/3o6ZsVGl3mKa4Vo1na/giphy.gif',
    'https://media.giphy.com/media/26tPplGWjN0xLybi5K/giphy.gif',
    'https://media.giphy.com/media/3o7aCTfyhYawdOXcFW/giphy.gif',
    'https://media.giphy.com/media/l41lFw057lAJQMhAI/giphy.gif',
    'https://media.giphy.com/media/26BRuo6sLetdllPAQ/giphy.gif',
    'https://media.giphy.com/media/3oKIPnAiaMCws8nOsE/giphy.gif',
    'https://media.giphy.com/media/xT9IgzCUT5hA2PudO4/giphy.gif',
    'https://media.giphy.com/media/l0MYEqEx19CO8XkJ6/giphy.gif'
  ];
  var MAX_LEN = 280;
  var GIF_RE = /^https?:\/\/\S+\.gif(\?.*)?$/i;

  var styleId = 'chat-panel-styles';
  function injectStyles() {
    if (document.getElementById(styleId)) return;
    var css = [
      '.chat-panel{display:flex;flex-direction:column;width:100%;height:100%;' +
        'background:rgba(255,255,255,.74);backdrop-filter:blur(20px);' +
        'border:1px solid rgba(255,255,255,.65);border-radius:14px;overflow:hidden;' +
        'box-shadow:0 10px 34px rgba(2,6,23,.22);font-family:inherit;}',
      '.chat-head{display:flex;align-items:center;gap:8px;padding:10px 12px;' +
        'border-bottom:1px solid rgba(6,182,212,.14);background:rgba(255,255,255,.5);cursor:pointer;user-select:none;}',
      '.chat-title{font-weight:700;font-size:.92rem;color:#0f172a;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      '.chat-unread{background:#ef4444;color:#fff;font-size:.68rem;font-weight:700;' +
        'border-radius:999px;padding:1px 7px;line-height:1.5;display:none;}',
      '.chat-unread.on{display:inline-block;}',
      '.chat-toggle{background:rgba(15,23,42,.06);border:none;border-radius:8px;width:26px;height:26px;' +
        'font-size:.85rem;cursor:pointer;color:#334155;line-height:1;}',
      '.chat-toggle:hover{background:rgba(15,23,42,.14);}',
      '.chat-body{display:flex;flex-direction:column;flex:1;min-height:0;position:relative;}',
      '.chat-panel.collapsed .chat-body{display:none;}',
      '.chat-msgs{flex:1;min-height:0;overflow-y:auto;padding:10px 10px 4px;display:flex;flex-direction:column;gap:6px;}',
      '.chat-msg{max-width:88%;align-self:flex-start;background:rgba(255,255,255,.85);' +
        'border:1px solid rgba(148,163,184,.28);border-radius:12px 12px 12px 4px;padding:6px 10px;position:relative;}',
      '.chat-msg.chat-mine{align-self:flex-end;background:rgba(6,182,212,.16);' +
        'border-color:rgba(6,182,212,.35);border-radius:12px 12px 4px 12px;}',
      '.chat-msg-head{display:flex;align-items:baseline;gap:8px;font-size:.66rem;font-weight:700;' +
        'color:#0ea5e9;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px;}',
      '.chat-msg.chat-mine .chat-msg-head{color:#0891b2;}',
      '.chat-msg-time{font-weight:500;color:#94a3b8;font-size:.62rem;text-transform:none;letter-spacing:0;}',
      '.chat-msg-body{font-size:.85rem;color:#0f172a;line-height:1.35;white-space:pre-wrap;word-break:break-word;}',
      '.chat-big-emoji{font-size:1.6rem;line-height:1.2;}',
      '.chat-msg-actions{position:absolute;top:-9px;right:6px;display:none;gap:4px;}',
      '.chat-msg:hover .chat-msg-actions{display:flex;}',
      '.chat-msg-actions button{background:rgba(15,23,42,.82);border:none;color:#fff;font-size:.66rem;' +
        'border-radius:6px;padding:2px 6px;cursor:pointer;}',
      '.chat-msg-actions button:hover{background:#0f172a;}',
      '.chat-reactions{display:flex;gap:4px;justify-content:center;padding:6px 8px 2px;flex-wrap:wrap;}',
      '.chat-reactions button{background:rgba(255,255,255,.8);border:1px solid rgba(148,163,184,.3);' +
        'border-radius:999px;font-size:1.05rem;padding:3px 8px;cursor:pointer;line-height:1.3;}',
      '.chat-reactions button:hover{background:rgba(6,182,212,.18);transform:scale(1.08);}',
      '.chat-input-row{display:flex;gap:6px;padding:8px;align-items:center;}',
      '.chat-emoji-btn{background:rgba(15,23,42,.06);border:none;border-radius:10px;width:34px;height:34px;' +
        'font-size:1.05rem;cursor:pointer;flex:none;}',
      '.chat-emoji-btn:hover{background:rgba(15,23,42,.14);}',
      '.chat-input{flex:1;min-width:0;background:rgba(255,255,255,.85);border:1px solid rgba(148,163,184,.4);' +
        'border-radius:10px;padding:8px 10px;font-size:.85rem;color:#0f172a;outline:none;}',
      '.chat-input:focus{border-color:#0ea5e9;box-shadow:0 0 0 2px rgba(14,165,233,.25);}',
      '.chat-input:disabled{opacity:.55;}',
      '.chat-send{background:#0ea5e9;color:#fff;border:none;border-radius:10px;width:38px;height:34px;' +
        'font-size:.95rem;cursor:pointer;flex:none;}',
      '.chat-send:hover{background:#0284c7;}',
      '.chat-send:disabled{opacity:.55;cursor:default;}',
      '.chat-emoji-grid{display:grid;grid-template-columns:repeat(8,1fr);gap:2px;padding:6px 8px;' +
        'background:rgba(255,255,255,.7);border-top:1px solid rgba(148,163,184,.25);max-height:130px;overflow-y:auto;}',
      '.chat-emoji-grid button{background:none;border:none;font-size:1.05rem;border-radius:6px;padding:2px;cursor:pointer;}',
      '.chat-emoji-grid button:hover{background:rgba(6,182,212,.18);}',
      '.chat-gif-btn{background:rgba(15,23,42,.06);border:none;border-radius:10px;width:34px;height:34px;' +
        'font-size:.78rem;font-weight:700;cursor:pointer;flex:none;color:#0ea5e9;}',
      '.chat-gif-btn:hover{background:rgba(15,23,42,.14);}',
      '.chat-gif-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;padding:6px 8px;' +
        'background:rgba(255,255,255,.7);border-top:1px solid rgba(148,163,184,.25);max-height:180px;overflow-y:auto;}',
      '.chat-gif-grid button{border:1px solid rgba(148,163,184,.2);border-radius:8px;overflow:hidden;padding:0;cursor:pointer;background:#fff;height:64px;}',
      '.chat-gif-grid button img{width:100%;height:100%;object-fit:cover;display:block;}',
      '.chat-gif-grid button:hover{border-color:#0ea5e9;transform:scale(1.02);}',
      '.chat-gif{max-width:220px;border-radius:10px;overflow:hidden;display:block;margin-top:4px;cursor:zoom-in;border:1px solid rgba(148,163,184,.25);}',
      '.chat-gif img{width:100%;height:auto;display:block;}',
      '.chat-toast{position:absolute;left:10px;right:10px;bottom:58px;background:#0f172a;color:#fff;' +
        'font-size:.75rem;border-radius:8px;padding:6px 10px;opacity:0;pointer-events:none;transition:opacity .2s;text-align:center;}',
      '.chat-toast.on{opacity:.95;}',
      '.chat-hint{font-size:.72rem;color:#64748b;text-align:center;padding:6px 8px 2px;}'
    ].join('\n');
    var style = document.createElement('style');
    style.id = styleId;
    style.textContent = css;
    document.head.appendChild(style);
  }

  function ChatPanel(opts) {
    opts = opts || {};
    this.opts = {
      scope: opts.scope || 'match',
      scopeId: opts.scopeId || null,
      withUid: opts.withUid || null,
      name: opts.name || 'Chat',
      quickReactions: !!opts.quickReactions,
      pollMs: opts.pollMs || 2000,
      collapsed: opts.collapsed !== false,
      maxHeight: opts.maxHeight || '380px',
      mount: opts.mount || null
    };
    this.after = -1;
    this.me = null;
    this.msgs = new Map();
    this.unread = 0;
    this._timer = null;
    this._destroyed = false;
    injectStyles();
    this._build();
    if (this.opts.scopeId || this.opts.withUid) this.start();
  }

  ChatPanel.prototype._build = function () {
    var self = this;
    var mount = this.opts.mount;
    if (!mount) return;

    var panel = document.createElement('div');
    panel.className = 'chat-panel' + (this.opts.collapsed ? ' collapsed' : '');
    panel.style.maxHeight = this.opts.maxHeight;

    var head = document.createElement('div');
    head.className = 'chat-head';

    this._titleEl = document.createElement('span');
    this._titleEl.className = 'chat-title';
    this._titleEl.textContent = this.opts.name;

    this._badgeEl = document.createElement('span');
    this._badgeEl.className = 'chat-unread';

    this._toggleEl = document.createElement('button');
    this._toggleEl.className = 'chat-toggle';
    this._toggleEl.type = 'button';
    this._toggleEl.textContent = this.opts.collapsed ? '＋' : '−';
    this._toggleEl.title = this.opts.collapsed ? 'Open chat' : 'Minimize chat';
    this._toggleEl.addEventListener('click', function (e) {
      e.stopPropagation();
      self.toggle();
    });
    head.addEventListener('click', function () { self.toggle(); });

    head.appendChild(this._titleEl);
    head.appendChild(this._badgeEl);
    head.appendChild(this._toggleEl);
    panel.appendChild(head);

    var body = document.createElement('div');
    body.className = 'chat-body';

    this._list = document.createElement('div');
    this._list.className = 'chat-msgs';
    body.appendChild(this._list);

    this._hintEl = document.createElement('div');
    this._hintEl.className = 'chat-hint';
    this._hintEl.style.display = 'none';
    body.appendChild(this._hintEl);

    if (this.opts.quickReactions) {
      var row = document.createElement('div');
      row.className = 'chat-reactions';
      QUICK_REACTIONS.forEach(function (emoji) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = emoji;
        b.title = 'Send ' + emoji;
        b.addEventListener('click', function () { self.send(emoji); });
        row.appendChild(b);
      });
      body.appendChild(row);
    }

    var inputRow = document.createElement('div');
    inputRow.className = 'chat-input-row';

    this._emojiBtn = document.createElement('button');
    this._emojiBtn.type = 'button';
    this._emojiBtn.className = 'chat-emoji-btn';
    this._emojiBtn.textContent = '😀';
    this._emojiBtn.title = 'Emoji';
    this._emojiBtn.addEventListener('click', function () {
      self._gifGrid.style.display = 'none';
      self._grid.style.display = (self._grid.style.display === 'grid') ? 'none' : 'grid';
    });
    inputRow.appendChild(this._emojiBtn);

    this._gifBtn = document.createElement('button');
    this._gifBtn.type = 'button';
    this._gifBtn.className = 'chat-gif-btn';
    this._gifBtn.textContent = 'GIF';
    this._gifBtn.title = 'GIF';
    this._gifBtn.addEventListener('click', function () {
      self._grid.style.display = 'none';
      self._gifGrid.style.display = (self._gifGrid.style.display === 'grid') ? 'none' : 'grid';
    });
    inputRow.appendChild(this._gifBtn);

    this._input = document.createElement('input');
    this._input.type = 'text';
    this._input.className = 'chat-input';
    this._input.maxLength = MAX_LEN;
    this._input.placeholder = 'Message…';
    this._input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); self.send(self._input.value); }
    });
    inputRow.appendChild(this._input);

    this._sendBtn = document.createElement('button');
    this._sendBtn.type = 'button';
    this._sendBtn.className = 'chat-send';
    this._sendBtn.textContent = '➤';
    this._sendBtn.title = 'Send';
    this._sendBtn.addEventListener('click', function () { self.send(self._input.value); });
    inputRow.appendChild(this._sendBtn);

    body.appendChild(inputRow);

    this._grid = document.createElement('div');
    this._grid.className = 'chat-emoji-grid';
    this._grid.style.display = 'none';
    EMOJI_GRID.forEach(function (emoji) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = emoji;
      b.addEventListener('click', function () {
        self._input.value += emoji;
        self._input.focus();
      });
      self._grid.appendChild(b);
    });
    body.appendChild(this._grid);

    this._gifGrid = document.createElement('div');
    this._gifGrid.className = 'chat-gif-grid';
    this._gifGrid.style.display = 'none';
    GIF_GRID.forEach(function (url) {
      var b = document.createElement('button');
      b.type = 'button';
      var img = document.createElement('img');
      img.src = url; img.loading = 'lazy'; img.alt = 'gif';
      b.appendChild(img);
      b.addEventListener('click', function () { self.send(url); self._gifGrid.style.display='none'; });
      self._gifGrid.appendChild(b);
    });
    body.appendChild(this._gifGrid);

    this._toastEl = document.createElement('div');
    this._toastEl.className = 'chat-toast';
    body.appendChild(this._toastEl);

    panel.appendChild(body);
    mount.appendChild(panel);
    this._el = panel;

    if (!this.opts.scopeId && !this.opts.withUid) this._setEnabled(false, 'Select a conversation to start chatting');
  };

  ChatPanel.prototype._setEnabled = function (enabled, hint) {
    this._input.disabled = !enabled;
    this._sendBtn.disabled = !enabled;
    if (enabled) {
      this._input.placeholder = 'Message…';
      this._hintEl.style.display = 'none';
    } else {
      this._hintEl.style.display = 'block';
      this._hintEl.textContent = hint || 'Chat unavailable';
    }
  };

  ChatPanel.prototype.start = function () {
    var self = this;
    if (this._timer || this._destroyed) return;
    this._setEnabled(true);
    this.poll();
    this._timer = setInterval(function () { self.poll(); }, this.opts.pollMs);
  };

  ChatPanel.prototype.stop = function () {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  };

  ChatPanel.prototype.destroy = function () {
    this.stop();
    this._destroyed = true;
    if (this._el && this._el.parentNode) this._el.parentNode.removeChild(this._el);
  };

  ChatPanel.prototype.toggle = function () {
    var collapsed = !this._el.classList.contains('collapsed');
    this._el.classList.toggle('collapsed', collapsed);
    this._toggleEl.textContent = collapsed ? '＋' : '−';
    this._toggleEl.title = collapsed ? 'Open chat' : 'Minimize chat';
    if (!collapsed) {
      this.unread = 0;
      this._setBadge();
      this._scrollToBottom(true);
      this.poll();
    }
  };

  ChatPanel.prototype.open = function () {
    if (this._el.classList.contains('collapsed')) this.toggle();
  };

  ChatPanel.prototype.setConversation = function (scopeId, name) {
    this.opts.scopeId = scopeId;
    this.opts.withUid = null;
    this._titleEl.textContent = name || this.opts.name;
    this.after = -1;
    this.unread = 0;
    this._setBadge();
    this._list.innerHTML = '';
    this.msgs.clear();
    this._setEnabled(true);
    this.start();
    this.poll();
  };

  ChatPanel.prototype.setRecipient = function (uid, name) {
    this.opts.withUid = uid;
    this.opts.scopeId = null;
    this._titleEl.textContent = name || this.opts.name;
    this.after = -1;
    this.unread = 0;
    this._setBadge();
    this._list.innerHTML = '';
    this.msgs.clear();
    this._setEnabled(true);
    this.start();
    this.poll();
  };

  ChatPanel.prototype._setBadge = function () {
    if (this.unread > 0) {
      this._badgeEl.textContent = this.unread > 99 ? '99+' : String(this.unread);
      this._badgeEl.classList.add('on');
    } else {
      this._badgeEl.classList.remove('on');
    }
  };

  ChatPanel.prototype._scrollToBottom = function (force) {
    var near = this._list.scrollHeight - this._list.scrollTop - this._list.clientHeight < 60;
    if (force || near) this._list.scrollTop = this._list.scrollHeight;
  };

  ChatPanel.prototype.poll = function () {
    var self = this;
    if (this._destroyed) return;
    var p = new URLSearchParams({ scope: this.opts.scope, limit: '60' });
    if (this.opts.scopeId) p.set('scope_id', this.opts.scopeId);
    else if (this.opts.withUid) p.set('with', this.opts.withUid);
    p.set('after', String(this.after));
    if (this.opts.scope === 'dm' && !this._el.classList.contains('collapsed')) p.set('mark_read', '1');
    fetch('/chat/messages?' + p.toString())
      .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, d: d }; }); })
      .then(function (r) {
        if (self._destroyed) return;
        if (!r.ok) return;
        var d = r.d;
        if (d.me) self.me = d.me;
        if (d.scope_id) self.opts.scopeId = d.scope_id;
        (d.messages || []).forEach(function (m) { self._append(m); });
        var na = parseInt(d.next_after, 10);
        if (!isNaN(na)) self.after = na;
      })
      .catch(function () { /* network blip — retry next tick */ });
  };

  ChatPanel.prototype._append = function (m) {
    if (this.msgs.has(m.mid)) return;
    var self = this;
    var mine = !!this.me && m.sender_id === this.me;

    var row = document.createElement('div');
    row.className = 'chat-msg' + (mine ? ' chat-mine' : '');

    var head = document.createElement('div');
    head.className = 'chat-msg-head';
    var who = document.createElement('span');
    who.textContent = mine ? 'You' : (m.sender_name || 'Player');
    head.appendChild(who);
    var time = document.createElement('span');
    time.className = 'chat-msg-time';
    var d = new Date(typeof m.ts === 'string' ? m.ts : (m.ts || 0) * 1000);
    if (!isNaN(d.getTime())) {
      time.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      time.title = d.toLocaleString();
    }
    head.appendChild(time);
    row.appendChild(head);

    var body = document.createElement('div');
    body.className = 'chat-msg-body' + (m.emoji_only ? ' chat-big-emoji' : '');
    var isGif = typeof m.body === 'string' && GIF_RE.test(m.body.trim());
    if (isGif) {
      var a = document.createElement('a');
      a.className = 'chat-gif'; a.href = m.body.trim(); a.target = '_blank'; a.rel = 'noopener';
      var img = document.createElement('img');
      img.src = m.body.trim(); img.alt = 'gif'; img.loading = 'lazy';
      a.appendChild(img);
      body.appendChild(a);
      if (m.body.trim() !== m.body) {
        var t = document.createElement('div'); t.textContent = m.body; t.style.display='none'; body.appendChild(t);
      }
    } else {
      body.textContent = m.body;
    }
    row.appendChild(body);

    if (!mine) {
      var actions = document.createElement('div');
      actions.className = 'chat-msg-actions';
      var rep = document.createElement('button');
      rep.type = 'button';
      rep.textContent = ' Report';
      rep.title = 'Report this message';
      rep.addEventListener('click', function () { self._report(m); });
      var blk = document.createElement('button');
      blk.type = 'button';
      blk.textContent = ' Block';
      blk.title = 'Block this user';
      blk.addEventListener('click', function () { self._block(m); });
      actions.appendChild(rep);
      actions.appendChild(blk);
      row.appendChild(actions);
    }

    this._list.appendChild(row);
    this.msgs.set(m.mid, { el: row, senderId: m.sender_id });
    if (this._el.classList.contains('collapsed')) {
      this.unread++;
      this._setBadge();
    } else {
      this._scrollToBottom(false);
    }
  };

  ChatPanel.prototype.send = function (body) {
    var self = this;
    body = (body || '').trim();
    if (!body) return;
    if (body.length > MAX_LEN) {
      this._toast('Message too long (max ' + MAX_LEN + ' characters)');
      return;
    }
    var payload = { scope: this.opts.scope, body: body };
    if (this.opts.scope === 'dm') payload.to_uid = this.opts.withUid;
    else payload.scope_id = this.opts.scopeId;
    if (!payload.scope_id && !payload.to_uid) return;
    fetch('/chat/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, d: d }; }); })
      .then(function (r) {
        if (self._destroyed) return;
        if (!r.ok) {
          var d = r.d || {};
          self._toast(d.error || 'Could not send message');
          if (d.retry_after) {
            setTimeout(function () { self.poll(); }, d.retry_after * 1000);
          }
          return;
        }
        if (r.d.message) {
          if (r.d.message.scope_id) {
            self.opts.scopeId = r.d.message.scope_id;
            self.opts.withUid = null;
          }
          if (r.d.message.sender_id) self.me = r.d.message.sender_id;
          self._append(r.d.message);
        }
        self._input.value = '';
        self._input.focus();
      })
      .catch(function () { self._toast('Could not send message'); });
  };

  ChatPanel.prototype._report = function (m) {
    var self = this;
    var reason = window.prompt('Reason for reporting this message? (optional)', '');
    if (reason === null) return;
    fetch('/chat/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: this.opts.scope, scope_id: this.opts.scopeId, mid: m.mid, reason: reason })
    })
      .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, d: d }; }); })
      .then(function (r) {
        self._toast(r.ok ? 'Report submitted — thanks.' : (r.d.error || 'Report failed'));
      })
      .catch(function () { self._toast('Report failed'); });
  };

  ChatPanel.prototype._block = function (m) {
    var self = this;
    var name = m.sender_name || 'this user';
    if (!window.confirm('Block ' + name + '? Their messages will be hidden everywhere for you.')) return;
    fetch('/chat/block', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: m.sender_id })
    })
      .then(function (resp) { return resp.json().then(function (d) { return { ok: resp.ok, d: d }; }); })
      .then(function (r) {
        if (!r.ok) { self._toast(r.d.error || 'Could not block user'); return; }
        self._toast('Blocked ' + name);
        self.msgs.forEach(function (rec, mid) {
          if (rec.senderId === m.sender_id) {
            rec.el.remove();
            self.msgs.delete(mid);
          }
        });
      })
      .catch(function () { self._toast('Could not block user'); });
  };

  ChatPanel.prototype._toast = function (text) {
    var self = this;
    this._toastEl.textContent = text;
    this._toastEl.classList.add('on');
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(function () {
      self._toastEl.classList.remove('on');
    }, 2600);
  };

  window.ChatPanel = ChatPanel;
})();
