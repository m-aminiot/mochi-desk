const $ = (selector) => document.querySelector(selector);
const messages = $('#messages');
const input = $('#message-input');
let cursor = 0;
let activeChatId = null;
let busy = false;
let stopping = false;
let syncing = false;
let voiceActive = false;
let approvals = [];
let waitingForApproval = null;
let preferences = {};

async function request(path, data) {
  const response = await fetch(path, {
    method: data === undefined ? 'GET' : 'POST',
    headers: data === undefined ? {} : {'Content-Type': 'application/json'},
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}

function setStatus(text, kind = '') {
  const status = $('#status');
  status.textContent = text;
  status.className = `composer-status ${kind ? `status-${kind}` : ''}`;
}

function scrollBottom() {
  const pane = $('#conversation');
  pane.scrollTop = pane.scrollHeight;
}

function addMessage(role, content, options = {}) {
  $('#welcome').classList.add('hidden');
  const row = document.createElement('article');
  row.className = `message-row ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = content;
  row.append(bubble);
  if (role === 'assistant' && !options.noActions) {
    const actions = document.createElement('div');
    actions.className = 'message-actions';
    const copy = document.createElement('button');
    copy.textContent = '▢';
    copy.title = 'Copy response';
    copy.onclick = async () => {
      await navigator.clipboard.writeText(content);
      copy.textContent = '✓';
      setTimeout(() => copy.textContent = '▢', 1000);
    };
    actions.append(copy);
    row.append(actions);
  }
  messages.append(row);
  scrollBottom();
  return row;
}

function addTool(event) {
  $('#welcome').classList.add('hidden');
  if (event.mode === 'voice') {
    const voiceCard = document.createElement('div');
    voiceCard.className = 'voice-tool-card';
    const voiceTitle = document.createElement('strong');
    const labels = {awaiting_approval: 'Awaiting approval', running: 'Running', completed: 'Complete', failed: 'Failed', cancelled: 'Cancelled'};
    voiceTitle.textContent = `${event.title || 'Tool'} · ${labels[event.state] || event.state}`;
    const voiceBody = document.createElement('div');
    const result = event.result || {};
    voiceBody.textContent = event.detail || result.message || result.error || '';
    voiceCard.append(voiceTitle, voiceBody);
    $('#voice-tools').prepend(voiceCard);
  }
  const card = document.createElement('div');
  card.className = 'tool-card';
  const title = document.createElement('strong');
  const labels = {awaiting_approval: 'Awaiting approval', running: 'Running', completed: 'Complete', failed: 'Failed', cancelled: 'Cancelled'};
  title.textContent = `${event.title || 'Tool'} · ${labels[event.state] || event.state}`;
  const body = document.createElement('span');
  const result = event.result || {};
  body.textContent = event.state === 'completed'
    ? (result.message || [result.stdout, result.stderr].filter(Boolean).join('\n') || 'The operation completed successfully.')
    : event.state === 'failed' ? (result.error || result.stderr || 'The tool could not complete.')
      : event.detail || '';
  card.append(title, body);
  messages.append(card);
  scrollBottom();
}

function renderChats(chats, selectedId) {
  const list = $('#chat-list');
  list.replaceChildren();
  chats.forEach((chat) => {
    const row = document.createElement('div');
    row.className = `chat-item ${chat.id === selectedId ? 'active' : ''}`;
    row.onclick = () => callAction('/api/select-chat', {id: chat.id});
    const title = document.createElement('span');
    title.className = 'chat-item-title';
    title.textContent = chat.title || 'New chat';
    const preview = document.createElement('span');
    preview.className = 'chat-item-preview';
    preview.textContent = chat.preview || 'New conversation';
    const remove = document.createElement('button');
    remove.className = 'delete-chat';
    remove.textContent = '×';
    remove.title = 'Delete chat';
    remove.onclick = (event) => {
      event.stopPropagation();
      if (confirm(`Delete “${chat.title}” and its saved history?`)) callAction('/api/delete-chat', {id: chat.id});
    };
    row.append(title, preview, remove);
    list.append(row);
  });
}

function renderHistory(state) {
  activeChatId = state.active_chat_id;
  $('#chat-title').textContent = state.active_title || 'Mochi';
  messages.replaceChildren();
  if (!state.messages.length) $('#welcome').classList.remove('hidden');
  else {
    $('#welcome').classList.add('hidden');
    state.messages.forEach((message) => addMessage(message.role, message.content));
  }
  renderChats(state.chats || [], activeChatId);
  scrollBottom();
}

function setBusy(value) {
  busy = value;
  if (!busy) stopping = false;
  const button = $('#send-button');
  button.classList.toggle('stop', busy);
  button.disabled = busy && stopping;
  button.textContent = stopping ? '…' : busy ? '■' : '↑';
  button.title = stopping ? 'Stopping…' : busy ? 'Stop' : 'Send';
}

async function callAction(path, data) {
  try {
    await request(path, data);
    const state = await request('/api/state');
    cursor = state.next_event_id;
    renderHistory(state);
    if (path === '/api/new-chat') input.focus();
  } catch (error) { setStatus(error.message, 'error'); }
}

function showNextApproval() {
  if (waitingForApproval || !approvals.length) return;
  waitingForApproval = approvals.shift();
  $('#approval-command').textContent = waitingForApproval.command;
  $('#approval-directory').textContent = waitingForApproval.directory || '';
  $('#approval-overlay').classList.remove('hidden');
}

async function decideApproval(approved) {
  if (!waitingForApproval) return;
  const item = waitingForApproval;
  waitingForApproval = null;
  $('#approval-overlay').classList.add('hidden');
  try { await request(approved ? '/api/approve' : '/api/reject', {id: item.id}); }
  catch (error) { setStatus(error.message, 'error'); }
  showNextApproval();
}

function openSettings(state) {
  preferences = state.preferences || preferences;
  const themes = state.themes || ['Violet', 'Ocean', 'Rose'];
  const models = state.models || [];
  $('#theme-select').replaceChildren(...themes.map((name) => new Option(name, name)));
  $('#theme-select').value = preferences.theme || 'Violet';
  $('#settings-model-select').replaceChildren(...models.map(([label, value]) => new Option(label, value)));
  $('#settings-model-select').value = preferences.model || 'gemini-3.5-flash-lite';
  $('#api-key-input').value = '';
  $('#api-base-url-input').value = preferences.api_base_url || '';
  $('#use-custom-api-base-url').checked = Boolean(preferences.use_custom_api_base_url);
  $('#api-base-url-input').disabled = !$('#use-custom-api-base-url').checked;
  $('#settings-overlay').classList.remove('hidden');
}

function renderSetupWarning(state) {
  const missing = [];
  if (!state.api_key_configured) missing.push('a Gemini API key');
  if (state.preferences?.use_custom_api_base_url && !state.api_base_url_configured) missing.push('a custom API base URL');
  const warning = $('#setup-warning');
  if (!missing.length) {
    warning.classList.add('hidden');
    return;
  }
  $('#setup-warning-text').textContent = `Add ${missing.join(' and ')} in Settings to connect Mochi.`;
  warning.classList.remove('hidden');
}

function applyTheme(theme) {
  const palettes = {
    Violet: {accent: '#b39aff', hover: '#9474ee', page: '#100f15', sidebar: '#17151e', surface: '#211e2a', strong: '#302a3d', wash: '#2c2540'},
    Ocean: {accent: '#48c8f5', hover: '#17a7dc', page: '#0b1216', sidebar: '#101c21', surface: '#17272d', strong: '#203b43', wash: '#183744'},
    Rose: {accent: '#ff62aa', hover: '#f23891', page: '#150b11', sidebar: '#24121c', surface: '#321923', strong: '#482033', wash: '#55223d'},
  };
  const palette = palettes[theme] || palettes.Violet;
  for (const [name, value] of Object.entries(palette)) document.documentElement.style.setProperty(`--${name}`, value);
}

function applyEvent(event) {
  if (event.type === 'user_message' && event.chat_id === activeChatId) addMessage('user', event.message);
  if (event.type === 'assistant_message' && event.chat_id === activeChatId) {
    addMessage('assistant', event.message);
    setStatus('Ready for your next message.');
  }
  if (event.type === 'tool') {
    addTool(event);
    if (event.state === 'awaiting_approval' && event.mode === 'text') setStatus('A command is waiting for your approval.');
  }
  if (event.type === 'approval') { approvals.push(event); showNextApproval(); }
  if (event.type === 'error') setStatus(event.message, 'error');
  if (event.type === 'busy') {
    setBusy(event.value);
    if (!event.value && /thinking|stop/i.test($('#status').textContent)) setStatus('Ready for your next message.');
  }
  if (event.type === 'voice_status') {
    $('#voice-status').textContent = event.message;
    const failed = event.message.startsWith('Voice error:');
    voiceActive = event.message.startsWith('Connected') || (voiceActive && !failed && event.message !== 'Voice session stopped');
    $('#voice-orb').classList.toggle('active', voiceActive);
    if (failed || event.message === 'Voice session stopped') voiceActive = false;
  }
  if (event.type === 'voice_text') $('#voice-reply').textContent = event.message;
  if (event.type === 'voice_status' && (event.message.startsWith('Voice error:') || event.message === 'Voice session stopped')) {
    voiceActive = false;
    $('#conversation').classList.remove('hidden');
    $('#voice-panel').classList.add('hidden');
    $('#composer-area').classList.remove('voice-mode');
  }
}

async function syncState(render = false) {
  if (syncing) return;
  syncing = true;
  try {
    const state = await request(`/api/state?since=${cursor}`);
    renderSetupWarning(state);
    if (render) renderHistory(state);
    else if (state.active_chat_id !== activeChatId) renderHistory(state);
    else renderChats(state.chats || [], activeChatId);
    cursor = Math.max(cursor, state.next_event_id || 0);
    setBusy(state.busy);
    preferences = state.preferences || preferences;
    applyTheme(preferences.theme);
    $('#model-select').replaceChildren(...(state.models || []).map(([label, value]) => new Option(label, value)));
    $('#model-select').value = preferences.model || 'gemini-3.5-flash-lite';
    if (state.startup_error) setStatus(`Setup could not finish: ${state.startup_error}`, 'error');
    if (state.voice_status) $('#voice-status').textContent = state.voice_status;
    if (state.voice_reply) $('#voice-reply').textContent = state.voice_reply;
    state.pending.forEach((item) => {
      if (item.id !== waitingForApproval?.id && !approvals.some((pending) => pending.id === item.id)) approvals.push(item);
    });
    showNextApproval();
    state.events.forEach(applyEvent);
    if (state.events.length) cursor = Math.max(cursor, ...state.events.map((event) => event.sequence));
  } catch (error) { setStatus(`Connection problem: ${error.message}`, 'error'); }
  finally { syncing = false; }
}

function submitMessage() {
  if (busy) {
    if (stopping) return;
    stopping = true;
    setBusy(true);
    setStatus('Stopping the request…');
    return request('/api/stop', {}).catch((error) => {
      stopping = false;
      setBusy(busy);
      setStatus(error.message, 'error');
    });
  }
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  resizeInput();
  request('/api/send', {message}).then(() => setStatus('Mochi is thinking…')).catch((error) => setStatus(error.message, 'error'));
}

function resizeInput() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

$('#send-button').onclick = submitMessage;
input.addEventListener('input', resizeInput);
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submitMessage(); }
});
$('#new-chat').onclick = () => callAction('/api/new-chat', {});
$('#new-chat-shortcut').onclick = () => callAction('/api/new-chat', {});
$('#menu-button').onclick = () => $('#sidebar').classList.add('open');
$('#sidebar-close').onclick = () => $('#sidebar').classList.remove('open');
$('#chat-list').addEventListener('click', (event) => { if (event.target.closest('.chat-item')) $('#sidebar').classList.remove('open'); });
$('#voice-toggle').onclick = async () => {
  const next = !voiceActive;
  try {
    await request('/api/voice', {active: next});
    voiceActive = next;
    if (next) {
      $('#voice-tools').replaceChildren();
      $('#voice-reply').textContent = '';
      preferences.voice_muted = false;
      $('#mute-voice').textContent = 'Mute microphone';
    }
    $('#conversation').classList.toggle('hidden', next);
    $('#voice-panel').classList.toggle('hidden', !next);
    $('#composer-area').classList.toggle('voice-mode', next);
    if (next) setStatus('Use End voice chat when you are finished.');
  } catch (error) { setStatus(error.message, 'error'); }
};
$('#mute-voice').onclick = async () => {
  preferences.voice_muted = !preferences.voice_muted;
  try {
    await request('/api/mute', {muted: preferences.voice_muted});
    $('#mute-voice').textContent = preferences.voice_muted ? 'Unmute microphone' : 'Mute microphone';
  } catch (error) { setStatus(error.message, 'error'); }
};
$('#stop-voice').onclick = async () => {
  try {
    await request('/api/voice', {active: false});
    voiceActive = false;
    $('#conversation').classList.remove('hidden');
    $('#voice-panel').classList.add('hidden');
    $('#composer-area').classList.remove('voice-mode');
  } catch (error) { setStatus(error.message, 'error'); }
};
$('#settings-open').onclick = async () => openSettings(await request('/api/state'));
$('#settings-shortcut').onclick = async () => openSettings(await request('/api/state'));
$('#setup-open-settings').onclick = async () => openSettings(await request('/api/state'));
$('#settings-close').onclick = $('#cancel-settings').onclick = () => $('#settings-overlay').classList.add('hidden');
$('#use-custom-api-base-url').onchange = (event) => {
  $('#api-base-url-input').disabled = !event.target.checked;
};
$('#save-settings').onclick = async () => {
  try {
    const apiKey = $('#api-key-input').value.trim();
    const result = await request('/api/settings', {
      theme: $('#theme-select').value,
      model: $('#settings-model-select').value,
      api_key: apiKey,
      api_base_url: $('#api-base-url-input').value.trim(),
      use_custom_api_base_url: $('#use-custom-api-base-url').checked,
    });
    $('#settings-overlay').classList.add('hidden');
    applyTheme($('#theme-select').value);
    setStatus('Settings saved.', 'ok');
    if (apiKey) setStatus(result.restart_required ? 'Key saved. Restart Mochi Desk to apply it.' : 'Key saved. Mochi Desk is ready.', 'ok');
    syncState(true);
  } catch (error) { setStatus(error.message, 'error'); }
};
$('#approve-command').onclick = () => decideApproval(true);
$('#reject-command').onclick = () => decideApproval(false);
$('#approval-overlay').addEventListener('click', (event) => { if (event.target.id === 'approval-overlay') decideApproval(false); });
$('#model-select').onchange = async () => {
  try { await request('/api/settings', {model: $('#model-select').value, theme: preferences.theme, api_base_url: preferences.api_base_url}); }
  catch (error) { setStatus(error.message, 'error'); }
};

async function boot() {
  try {
    const state = await request('/api/state');
    cursor = state.next_event_id || 0;
    renderHistory(state);
    setBusy(state.busy);
    preferences = state.preferences || {};
    applyTheme(preferences.theme);
    renderSetupWarning(state);
    $('#model-select').replaceChildren(...(state.models || []).map(([label, value]) => new Option(label, value)));
    $('#model-select').value = preferences.model || '';
    $('#voice-status').textContent = state.voice_status || '';
    state.pending.forEach((item) => approvals.push(item));
    showNextApproval();
    if (state.startup_error) setStatus(`Setup could not finish: ${state.startup_error}`, 'error');
    setInterval(() => syncState(false), 650);
  } catch (error) { setStatus(`App is unavailable: ${error.message}`, 'error'); }
}
boot();
