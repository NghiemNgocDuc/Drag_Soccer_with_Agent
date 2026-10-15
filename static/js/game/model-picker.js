// Event-driven opponent library. Names and descriptions from uploads stay text.
const STYLES = {
  greedy: ['Direct', 'A direct, goal-focused opponent.'],
  team_coordinated: ['Teamwork', 'Finds passing lanes, makes support runs and keeps defensive cover.'],
  possession: ['Patient', 'Keeps the ball with short passes and well-placed support.'],
  counter_attack: ['Attacking', 'Moves forward quickly with direct passes and shots.'],
  high_press: ['Pressure', 'Closes down the ball and contests possession.'],
  potential_field: ['Space', 'Steers toward goal while avoiding defenders.'],
  voronoi: ['Space', 'Looks for space your team is leaving open.'],
  genetic_fuzzy: ['Adaptive', 'Balances distance and shooting angles to choose a move.'],
  a2c_lite: ['Planning', 'Compares possible moves for a better next position.'],
  expectimax: ['Planning', 'Weighs its next move against possible replies.'],
  adaptive_learner: ['Learning', 'Uses experience from previous games to choose its moves.'],
  goalnet_gat: ['Teamwork', 'Evaluates player connections and attacking opportunities.'],
  edms: ['All-round', 'Balances space, passing options and chances to score.'],
  hybrid_ensemble: ['All-round', 'Combines several approaches to decide its next move.'],
  langchain: ['Tactical', 'Uses physics-checked tactics to plan its next move.'],
};

export function createModelPicker({onChoose, onApplied = () => {}}) {
  const byId = id => document.getElementById(id);
  const modal = byId('match-models-modal'), select = byId('model-select');
  const list = byId('match-models-list'), search = byId('model-search');
  const apply = byId('model-picker-apply'), retry = byId('model-picker-retry');
  const empty = byId('model-picker-empty'), status = byId('model-picker-status');
  const inlineStatus = byId('model-select-status');
  const filters = [...modal.querySelectorAll('[data-model-filter]')];
  let models = [], current = select.value, selected = current, filter = 'all';
  let loading = null, loaded = false, loadError = false, saving = false, disabled = false;
  let play = false, openGeneration = 0, restoreOverflow = null;
  const describe = model => model.type === 'user'
    ? [ 'My model', (model.desc || 'Your custom playing style.').replace(/\s*\[My model\]\s*$/, '') ]
    : STYLES[model.id] || ['Built-in', model.desc || 'A built-in opponent.'];
  const isOpen = () => modal.style.display === 'flex';
  const message = (text = '', error = false) => {
    for (const el of [status, inlineStatus]) {
      el.textContent = text;
      el.classList.toggle('is-error', error);
    }
  };
  function syncControls() {
    select.disabled = disabled || saving || !loaded;
    byId('browse-models-btn').disabled = disabled || saving;
    apply.disabled = disabled || saving || !models.some(m => m.id === selected);
    apply.textContent = saving ? 'Saving…' : play ? 'Play against model' : 'Use opponent';
    byId('model-picker-selected').textContent = models.find(m => m.id === selected)?.name || 'Choose a model';
    modal.setAttribute('aria-busy', String(saving || !!loading));
    for (const input of list.querySelectorAll('input')) input.disabled = saving;
  }
  function setCurrent(id) {
    if (saving || !id) return;
    current = id;
    select.value = id;
    const model = models.find(m => m.id === id);
    byId('model-summary').textContent = model ? describe(model)[1] : 'Browse the library to choose your opponent.';
  }
  function render() {
    list.replaceChildren();
    const query = search.value.trim().toLowerCase();
    const shown = models.filter(m => (filter === 'all' || m.type === filter)
      && [m.name, m.desc, ...describe(m)].join(' ').toLowerCase().includes(query));
    byId('model-results-count').textContent = loaded ? `${shown.length} ${shown.length === 1 ? 'model' : 'models'}` : '';
    retry.hidden = !loadError;
    empty.hidden = shown.length > 0;
    empty.textContent = loading ? 'Loading opponents…' : loadError ? 'Could not load models. Check your connection and try again.'
      : !models.length ? 'No models are available yet.' : filter === 'user' && !query
        ? 'Your custom models will appear here. Create one in My models to get started.'
        : 'No models match. Try a different search or filter.';
    for (const model of shown) {
      const [style, description] = describe(model);
      const label = document.createElement('label'); label.className = 'model-option';
      const input = document.createElement('input');
      input.type = 'radio'; input.name = 'opponent-model'; input.value = model.id;
      input.checked = model.id === selected;
      const copy = document.createElement('span'); copy.className = 'model-option-copy';
      const tag = document.createElement('span'); tag.className = 'model-option-tag'; tag.textContent = style;
      const name = document.createElement('strong'); name.textContent = model.name;
      const desc = document.createElement('span'); desc.className = 'model-option-desc'; desc.textContent = description;
      copy.append(tag, name, desc); label.append(input, copy); list.append(label);
      input.addEventListener('change', () => { selected = model.id; message(); syncControls(); });
    }
    filters.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.modelFilter === filter)));
    syncControls();
  }
  async function load() {
    if (loaded) return;
    if (loading) return loading;
    loadError = false;
    loading = (async () => {
      try {
        const response = await fetch('/models');
        if (!response.ok) throw new Error('Catalog unavailable');
        const data = await response.json();
        if (!Array.isArray(data)) throw new Error('Invalid catalog');
        models = data.filter(m => m && typeof m.id === 'string' && typeof m.name === 'string');
        select.replaceChildren();
        for (const [type, title] of [['builtin', 'Built-in opponents'], ['user', 'My models']]) {
          const group = document.createElement('optgroup'); group.label = title;
          for (const model of models.filter(m => m.type === type)) {
            const option = document.createElement('option'); option.value = model.id; option.textContent = model.name;
            group.append(option);
          }
          if (group.children.length) select.append(group);
        }
        loaded = true; message(); setCurrent(current);
      } catch (_) {
        loadError = true; message('Could not load models. Open Browse models to retry.', true);
      } finally { loading = null; render(); }
    })();
    render();
    return loading;
  }
  function close() {
    openGeneration++;
    modal.style.display = 'none';
    if (restoreOverflow !== null) { document.body.style.overflow = restoreOverflow; restoreOverflow = null; }
    window.AgentSoccerDialogs.close(modal);
  }
  async function choose(id, fromDialog = false) {
    if (saving || disabled) { select.value = current; return; }
    const generation = openGeneration, startPlay = fromDialog && play;
    saving = true; message('Saving opponent…'); syncControls();
    try {
      await onChoose(id, {play: startPlay});
      saving = false; setCurrent(id); syncControls();
      message(`${models.find(m => m.id === id)?.name || 'Opponent'} selected.`);
      if (fromDialog && generation === openGeneration) close();
      onApplied({play: startPlay});
    } catch (error) {
      saving = false; setCurrent(error.committedModel || current);
      message(error.message || 'Could not change opponent. Try again.', true);
    } finally { syncControls(); }
  }
  function open(options = {}) {
    if (disabled || saving) return;
    play = !!options.play; selected = current; filter = 'all'; search.value = '';
    openGeneration++; message();
    if (restoreOverflow === null) { restoreOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; }
    modal.style.display = 'flex'; render();
    window.AgentSoccerDialogs.open(modal, search);
    modal.querySelector('.model-picker-results').scrollTop = 0;
    void load();
  }
  search.addEventListener('input', render);
  filters.forEach(button => button.addEventListener('click', () => { filter = button.dataset.modelFilter; render(); }));
  retry.addEventListener('click', () => { void load(); });
  apply.addEventListener('click', () => { void choose(selected, true); });
  modal.addEventListener('click', event => { if (event.target === modal) close(); });
  return {open, close, load, choose, setCurrent, setDisabled(value) { disabled = value; syncControls(); }};
}
