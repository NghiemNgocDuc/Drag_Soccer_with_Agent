// Native radio controls; updates occur only on input or an existing state sync.
const STYLES = [
  ['Deep defend', 'Keep a deep defensive shape and protect your goal.'],
  ['Defend', 'Hold your shape and offer safer passing options.'],
  ['Balanced', 'Balance defensive cover with forward support.'],
  ['Attack', 'Push support players higher and look for forward options.'],
  ['All-out attack', 'Commit more players forward, leaving less cover behind.'],
];

export function createPlaystyleControl({ getState, blockedReason, getContext, onSave }) {
  const field = document.getElementById('playstyle-control');
  const team = document.getElementById('playstyle-team');
  const teamChoice = document.getElementById('playstyle-team-choice');
  const teamLabel = document.getElementById('playstyle-team-label');
  const name = document.getElementById('playstyle-name');
  const help = document.getElementById('playstyle-help');
  const status = document.getElementById('playstyle-status');
  const radios = [...field.querySelectorAll('input[name="team-playstyle"]')];
  let saving = false, pending = null, message = '', error = false, context = '';

  const levelFor = (state, side) => {
    const value = state?.['playstyle_' + side];
    return Number.isInteger(value) && value >= 1 && value <= 5 ? value : 3;
  };
  function sync() {
    const state = getState(), nextContext = getContext();
    if (nextContext !== context && !saving) { message = ''; error = false; }
    context = nextContext;
    const both = state?.game_mode === 'aivai';
    if (!both) team.value = 'a';
    teamChoice.hidden = !both;
    teamLabel.hidden = both;
    const level = pending ?? levelFor(state, team.value);
    const reason = blockedReason();
    field.disabled = saving || !!reason;
    field.setAttribute('aria-busy', String(saving));
    field.dataset.level = String(level);
    for (const radio of radios) radio.checked = Number(radio.value) === level;
    name.textContent = STYLES[level - 1][0];
    help.textContent = STYLES[level - 1][1];
    status.textContent = saving ? 'Saving mentality…' : reason || message;
    status.classList.toggle('is-error', error && !reason);
  }
  async function choose(level) {
    const reason = blockedReason();
    if (saving || reason) { sync(); return; }
    const side = team.value;
    if (level === levelFor(getState(), side)) { sync(); return; }
    const wasFocused = radios.includes(document.activeElement);
    saving = true; pending = level; message = ''; error = false; sync();
    let applied = false;
    try {
      await onSave(level, side);
      applied = true;
      message = STYLES[level - 1][0] + ' saved.';
    } catch (failure) {
      if (failure.name !== 'AbortError') {
        message = failure.message || 'Could not save mentality. Try again.';
        error = true;
      }
    } finally {
      saving = false; pending = null; sync();
      if (wasFocused && !field.disabled && (applied || error)
          && (!document.activeElement || document.activeElement === document.body)) {
        radios.find(radio => radio.checked)?.focus();
      }
    }
  }
  for (const radio of radios) radio.addEventListener('change', () => { void choose(Number(radio.value)); });
  team.addEventListener('change', () => { message = ''; error = false; sync(); });
  return { sync };
}
