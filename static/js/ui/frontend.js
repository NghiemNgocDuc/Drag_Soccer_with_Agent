/* Shared navigation and accessible page behavior. No animation loop or polling. */
(() => {
  const navigation = document.getElementById('as-nav-links');
  const toggle = document.querySelector('.as-menu-toggle');
  const setOpen = open => {
    navigation?.classList.toggle('is-open', open);
    toggle?.setAttribute('aria-expanded', String(open));
    toggle?.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  };
  toggle?.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  document.querySelectorAll('.as-header details').forEach(menu => {
    menu.addEventListener('toggle', () => {
      if (!menu.open) return;
      document.querySelectorAll('.as-header details[open]').forEach(other => { if (other !== menu) other.open = false; });
    });
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.as-header')) {
      document.querySelectorAll('.as-header details[open]').forEach(menu => { menu.open = false; });
      setOpen(false);
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const menu = document.querySelector('.as-header details[open]');
    if (menu) { menu.open = false; menu.querySelector('summary')?.focus(); }
    else if (toggle?.getAttribute('aria-expanded') === 'true') { setOpen(false); toggle.focus(); }
  });
  const main = document.querySelector('main');
  if (main) { main.id ||= 'as-content'; main.tabIndex = -1; }
  document.querySelector('.as-skip')?.addEventListener('click', event => {
    if (main) { event.preventDefault(); main.focus(); main.scrollIntoView({block:'start'}); }
  });
  if (window.self !== window.top) document.body.classList.add('as-embedded');
  document.querySelectorAll('input[type=password]').forEach(input => {
    if (input.closest('.as-password')) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'as-password';
    input.parentNode.insertBefore(wrapper, input); wrapper.append(input);
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'as-password-toggle'; button.textContent = 'Show';
    button.setAttribute('aria-label', 'Show password'); button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      const show = input.type === 'password'; input.type = show ? 'text' : 'password';
      button.textContent = show ? 'Hide' : 'Show'; button.setAttribute('aria-pressed', String(show));
      button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    });
    wrapper.append(button);
  });
  document.querySelectorAll('.flash,.message,.form-status,[id$="-status"]').forEach(element => {
    if (!element.getAttribute('aria-live')) element.setAttribute('aria-live','polite');
  });
  const dialogs = [];
  const returnFocus = new Map();
  const visible = element => element && element.isConnected && element.getClientRects().length > 0;
  const itemsIn = modal => [...modal.querySelectorAll('button,input,select,a[href],textarea,[tabindex]')]
    .filter(item => !item.disabled && item.tabIndex >= 0 && visible(item) && item.closest('.online-modal') === modal);
  const activeDialog = () => [...dialogs].reverse().find(visible);
  window.AgentSoccerDialogs = {
    open(modal, initialFocus) {
      if (!returnFocus.has(modal)) returnFocus.set(modal, document.activeElement);
      const index = dialogs.indexOf(modal);
      if (index >= 0) dialogs.splice(index, 1);
      dialogs.push(modal);
      (visible(initialFocus) ? initialFocus : itemsIn(modal)[0])?.focus();
    },
    close(modal) {
      const index = dialogs.indexOf(modal);
      if (index >= 0) dialogs.splice(index, 1);
      const previous = returnFocus.get(modal);
      returnFocus.delete(modal);
      const parent = activeDialog();
      if (visible(previous) && !previous.disabled && (!parent || parent.contains(previous))) previous.focus();
      else if (parent) itemsIn(parent)[0]?.focus();
    }
  };
  document.querySelectorAll('.online-modal').forEach(modal => {
    const card = modal.querySelector('.online-modal-card');
    const heading = card?.querySelector('h3');
    if (card && heading) {
      heading.id ||= `${modal.id}-title`;
      card.setAttribute('role','dialog'); card.setAttribute('aria-modal','true');
      card.setAttribute('aria-labelledby', heading.id);
    }
  });
  document.addEventListener('keydown', event => {
    const modal = activeDialog();
    if (!modal) return;
    const feedback = document.getElementById('fb-overlay');
    if (feedback && !feedback.hidden && visible(feedback)) return;
    if (event.target.closest('[role="dialog"]') && !modal.contains(event.target)) return;
    const items = itemsIn(modal);
    if (event.key === 'Escape') {
      const close = items.find(item => item.hasAttribute('data-dialog-close') || /^Close(?:\s|$)/i.test(item.textContent.trim()));
      if (close) {event.preventDefault(); event.stopPropagation(); close.click();}
    } else if (event.key === 'Tab' && items.length) {
      if (!items.includes(document.activeElement)) {event.preventDefault(); (event.shiftKey ? items.at(-1) : items[0]).focus();}
      else if (event.shiftKey && document.activeElement === items[0]) {event.preventDefault(); items.at(-1).focus();}
      else if (!event.shiftKey && document.activeElement === items.at(-1)) {event.preventDefault(); items[0].focus();}
    }
  }, true);
})();
