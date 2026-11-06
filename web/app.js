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
let voiceLevels = {input: 0, output: 0};
let waveformFrame = 0;
let eventSocket = null;
let reconnectTimer = null;
let pendingAttachments = [];
let streamingDraft = null;

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

/** Escape model supplied text before inserting it into the message DOM. */
function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
}

/** Apply a small token color pass to escaped code; unknown languages stay readable. */
function highlightCode(code, language) {
  const escaped = escapeHtml(code);
  if (!/^(js|javascript|ts|typescript|py|python|json|css|html|bash|sh)$/i.test(language)) return escaped;
  return escaped.replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g, '<span class="code-string">$1</span>')
    .replace(/\b(const|let|var|function|return|if|else|for|while|class|new|async|await|def|import|from|true|false|null|None|print)\b/g, '<span class="code-keyword">$1</span>')
    .replace(/(\/\/[^\n]*|#[^\n]*)/g, '<span class="code-comment">$1</span>');
}

/** Render common Markdown blocks safely without allowing raw HTML through. */
function renderMarkdown(markdown) {
  const blocks = [];
  let source = markdown.replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_match, language, code) => {
    const index = blocks.push(`<pre class="code-block"><code>${highlightCode(code, language)}</code></pre>`) - 1;
    return `\n\n@@CODE${index}@@\n\n`;
  });
  source = escapeHtml(source);
  const inline = (line) => line.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  const html = source.split(/\n{2,}/).map((block) => {
    const codeMatch = block.match(/^@@CODE(\d+)@@$/);
    if (codeMatch) return blocks[Number(codeMatch[1])];
    const heading = block.match(/^(#{1,6})\s+(.+)$/);
    if (heading) return `<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`;
    if (/^[-*]\s/m.test(block)) return `<ul>${block.split('\n').map((line) => line.match(/^[-*]\s+(.*)$/)).filter(Boolean).map((item) => `<li>${inline(item[1])}</li>`).join('')}</ul>`;
    return `<p>${block.split('\n').map(inline).join('<br>')}</p>`;
  }).join('');
  return html.replace(/@@CODE(\d+)@@/g, (_match, index) => blocks[Number(index)] || '');
}

function addMessage(role, content, options = {}) {
  $('#welcome').classList.add('hidden');
  const row = document.createElement('article');
  row.className = `message-row ${role}`;
  row.dir = 'auto';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.dir = 'auto';
  if (role === 'assistant') {
    bubble.classList.add('markdown');
    bubble.innerHTML = renderMarkdown(content);
    // Add a copy control to each fenced code block after safe rendering.
    bubble.querySelectorAll('pre.code-block').forEach((pre) => {
      const code = pre.textContent;
      const button = document.createElement('button');
      button.className = 'code-copy';
      button.textContent = 'Copy';
      button.onclick = async () => {
        await navigator.clipboard.writeText(code);
        button.textContent = 'Copied';
        setTimeout(() => button.textContent = 'Copy', 1000);
      };
      pre.append(button);
    });
  } else {
    bubble.textContent = content;
    if (!content) bubble.hidden = true;
  }
  row.append(bubble);
  if (options.attachments?.length) {
    const attachments = document.createElement('div');
    attachments.className = 'message-attachments';
    options.attachments.forEach((file) => {
      const link = document.createElement('a');
      link.className = 'message-attachment';
      link.href = file.url || `/uploads/${encodeURIComponent(file.id)}`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      if (file.mime_type?.startsWith('image/')) {
        const image = document.createElement('img');
        image.src = link.href;
        image.alt = file.name || 'Attached image';
        link.append(image);
      } else {
        link.classList.add('file-attachment');
        link.textContent = `📄 ${file.name || 'Attached file'}`;
      }
      attachments.append(link);
    });
    row.append(attachments);
  }
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
  const rtl = /[\u0600-\u06ff]/.test(document.documentElement.lang);
  const labels = rtl ? ['امروز', 'دیروز', 'این هفته', 'قدیمی‌تر'] : ['Today', 'Yesterday', 'This week', 'Earlier'];
  const groups = new Map(labels.map((label) => [label, []]));
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  chats.forEach((chat) => {
    const timestamp = new Date(chat.updated_at || chat.created_at || Date.now());
    const day = new Date(timestamp.getFullYear(), timestamp.getMonth(), timestamp.getDate());
    const daysAgo = Math.floor((today - day) / 86400000);
    groups.get(labels[daysAgo <= 0 ? 0 : daysAgo === 1 ? 1 : daysAgo < 7 ? 2 : 3]).push(chat);
  });
  groups.forEach((items, label) => {
    if (!items.length) return;
    const heading = document.createElement('div');
    heading.className = 'chat-date-heading';
    heading.textContent = label;
    list.append(heading);
    items.forEach((chat) => {
    const row = document.createElement('div');
    row.className = `chat-item ${chat.id === selectedId ? 'active' : ''}`;
    row.onclick = () => callAction('/api/select-chat', {id: chat.id});
    const title = document.createElement('span');
    title.className = 'chat-item-title';
    title.dir = 'auto';
    title.textContent = chat.title || 'New chat';
    const icon = document.createElement('span');
    icon.className = 'chat-avatar';
    icon.setAttribute('aria-hidden', 'true');
    const topic = `${chat.title || ''} ${chat.preview || ''}`.toLowerCase();
    icon.textContent = /code|python|javascript|کد|برنامه/.test(topic) ? '💻' : /write|story|نوشت|متن|شعر/.test(topic) ? '✍️' : /idea|plan|ایده|برنامه‌ریزی/.test(topic) ? '💡' : /music|آهنگ|موسیقی/.test(topic) ? '🎵' : /image|عکس|تصویر/.test(topic) ? '🎨' : '💬';
    const preview = document.createElement('span');
    preview.className = 'chat-item-preview';
    preview.dir = 'auto';
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
  });
}

function renderHistory(state) {
  streamingDraft = null;
  activeChatId = state.active_chat_id;
  $('#chat-title').textContent = state.active_title || 'Mochi';
  messages.replaceChildren();
  if (!state.messages.length) $('#welcome').classList.remove('hidden');
  else {
    $('#welcome').classList.add('hidden');
    state.messages.forEach((message) => addMessage(message.role, message.content, {attachments: message.attachments}));
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
  const status = $('#status');
  status.classList.toggle('is-thinking', busy && !stopping);
  if (busy && !stopping && !status.classList.contains('status-error')) {
    status.replaceChildren(document.createTextNode('Mochi is thinking'));
    const dots = document.createElement('span');
    dots.className = 'typing-dots';
    dots.setAttribute('aria-hidden', 'true');
    dots.innerHTML = '<i></i><i></i><i></i>';
    status.append(dots);
  } else if (!busy && status.classList.contains('is-thinking')) {
    status.classList.remove('is-thinking');
    status.textContent = 'Ready for your next message.';
  }
}

/** Draw live microphone and assistant output levels on the voice canvas. */
function drawVoiceWaveform() {
  const canvas = $('#voice-waveform');
  if (!canvas) return;
  const bounds = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(bounds.width * ratio) || canvas.height !== Math.round(bounds.height * ratio)) {
    canvas.width = Math.round(bounds.width * ratio);
    canvas.height = Math.round(bounds.height * ratio);
  }
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, bounds.width, bounds.height);
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
  const inputLevel = Math.max(0, Math.min(1, voiceLevels.input || 0));
  const outputLevel = Math.max(0, Math.min(1, voiceLevels.output || 0));
  const energy = Math.max(inputLevel, outputLevel);
  const time = performance.now() / 1000;
  const centerX = bounds.width / 2;
  const centerY = bounds.height / 2;
  const baseRadius = Math.min(bounds.width, bounds.height) * .39;
  const gradient = context.createRadialGradient(centerX, centerY, baseRadius * .16, centerX, centerY, baseRadius * 1.2);
  gradient.addColorStop(0, `${accent}00`);
  gradient.addColorStop(.66, `${accent}${Math.round(22 + energy * 36).toString(16).padStart(2, '0')}`);
  gradient.addColorStop(1, `${accent}00`);
  context.fillStyle = gradient;
  context.fillRect(0, 0, bounds.width, bounds.height);
  for (let ring = 0; ring < 3; ring += 1) {
    const phase = time * (1.4 + ring * .18) + ring * 1.5;
    const radius = baseRadius * (1.06 + ring * .17 + Math.sin(phase) * (.025 + energy * .075));
    context.beginPath();
    context.ellipse(centerX, centerY, radius * (1 + outputLevel * .035), radius * (1 + inputLevel * .06), phase * .08, 0, Math.PI * 2);
    context.strokeStyle = ring === 1 ? '#65d7ff' : accent;
    context.globalAlpha = .22 + energy * .48 - ring * .045;
    context.lineWidth = 1.2 + energy * 1.5;
    context.shadowColor = ring === 1 ? '#65d7ff' : accent;
    context.shadowBlur = 10 + energy * 20;
    context.stroke();
  }
  context.shadowBlur = 0;
  for (let index = 0; index < 30; index += 1) {
    const angle = index * Math.PI * 2 / 30 + time * (index % 2 ? .12 : -.09);
    const jitter = Math.sin(time * 2.6 + index * 2.3) * (2 + energy * 10);
    const radius = baseRadius * (1.04 + (index % 4) * .075) + jitter;
    const x = centerX + Math.cos(angle) * radius;
    const y = centerY + Math.sin(angle) * radius;
    const dot = 1 + ((index * 7) % 3) + energy * 1.8;
    context.globalAlpha = .3 + .45 * Math.abs(Math.sin(time * 2 + index));
    context.fillStyle = index % 3 === 0 ? '#65d7ff' : accent;
    context.beginPath();
    context.arc(x, y, dot, 0, Math.PI * 2);
    context.fill();
  }
  context.globalAlpha = 1;
  context.shadowBlur = 0;
  $('#mic-level').style.transform = `scaleX(${Math.max(.08, inputLevel)})`;
  $('#assistant-level').style.transform = `scaleX(${Math.max(.08, outputLevel)})`;
  $('#voice-orb').style.setProperty('--voice-energy', energy);
  $('#voice-orb').style.setProperty('--voice-scale', 1 + energy * .13);
  $('#voice-orb').style.setProperty('--voice-x', `${(outputLevel - inputLevel) * 7}px`);
  $('#voice-orb').style.setProperty('--voice-y', `${(inputLevel - outputLevel) * 5}px`);
  waveformFrame = requestAnimationFrame(drawVoiceWaveform);
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
    Violet: {accent: '#b39aff', hover: '#9474ee', page: '#100f15', sidebar: '#17151e', surface: '#211e2a', strong: '#302a3d', wash: '#2c2540', text: '#e8e8e8', muted: '#a0a0a0', border: '#292631', glass: 'rgba(23, 21, 30, .78)'},
    Ocean: {accent: '#48c8f5', hover: '#17a7dc', page: '#0b1216', sidebar: '#101c21', surface: '#17272d', strong: '#203b43', wash: '#183744', text: '#e8e8e8', muted: '#a0a0a0', border: '#26363b', glass: 'rgba(16, 28, 33, .78)'},
    Rose: {accent: '#ff62aa', hover: '#f23891', page: '#150b11', sidebar: '#24121c', surface: '#321923', strong: '#482033', wash: '#55223d', text: '#e8e8e8', muted: '#a0a0a0', border: '#38202d', glass: 'rgba(36, 18, 28, .78)'},
    Emerald: {accent: '#54e0ad', hover: '#20bd89', page: '#0b1411', sidebar: '#111e19', surface: '#192b23', strong: '#234335', wash: '#1b4836', text: '#e8f3ed', muted: '#a0b2a8', border: '#293a31', glass: 'rgba(17, 30, 25, .78)'},
    Amber: {accent: '#ffd166', hover: '#f5ad32', page: '#17130b', sidebar: '#211b10', surface: '#302616', strong: '#49391e', wash: '#56401c', text: '#f3eee2', muted: '#b5aa91', border: '#3a301e', glass: 'rgba(33, 27, 16, .78)'},
    Midnight: {accent: '#9ba8ff', hover: '#7484f2', page: '#090d1c', sidebar: '#10162b', surface: '#1a2340', strong: '#28365f', wash: '#253466', text: '#e8ebff', muted: '#a1a9c5', border: '#252e4b', glass: 'rgba(16, 22, 43, .8)'},
    Light: {accent: '#7656d6', hover: '#6241c2', page: '#f4f2f8', sidebar: '#ebe8f2', surface: '#fff', strong: '#e3def0', wash: '#7656d6', text: '#252130', muted: '#726d7d', border: '#ded9e8', glass: 'rgba(255, 255, 255, .76)'},
  };
  const palette = palettes[theme] || palettes.Violet;
  for (const [name, value] of Object.entries(palette)) document.documentElement.style.setProperty(`--${name}`, value);
  document.documentElement.dataset.theme = theme === 'Light' ? 'light' : 'dark';
  const toggle = $('#theme-toggle');
  toggle.textContent = theme === 'Light' ? '🌙' : '☀️';
  toggle.title = theme === 'Light' ? 'Switch to dark theme' : 'Switch to light theme';
  toggle.setAttribute('aria-label', toggle.title);
}

/** Persist the selected appearance through the existing settings API. */
async function selectTheme(theme) {
  const previous = preferences.theme || 'Violet';
  try {
    await request('/api/settings', {theme, model: preferences.model, api_base_url: preferences.api_base_url});
    preferences.theme = theme;
    if (theme !== 'Light') preferences.last_dark_theme = theme;
    applyTheme(theme);
    if (!$('#settings-overlay').classList.contains('hidden')) $('#theme-select').value = theme;
  } catch (error) {
    applyTheme(previous);
    setStatus(error.message, 'error');
  }
}

function applyEvent(event) {
  if (event.type === 'voice_level') voiceLevels = event.levels || voiceLevels;
  if (event.type === 'user_message' && event.chat_id === activeChatId) addMessage('user', event.message, {attachments: event.attachments});
  if (event.type === 'assistant_delta' && event.chat_id === activeChatId) {
    if (!streamingDraft) {
      const row = addMessage('assistant', '', {noActions: true});
      const bubble = row.querySelector('.bubble');
      bubble.hidden = false;
      bubble.classList.add('streaming-text');
      streamingDraft = {row, bubble, content: ''};
    }
    streamingDraft.content += event.delta || '';
    streamingDraft.bubble.textContent = streamingDraft.content;
    scrollBottom();
  }
  if (event.type === 'assistant_message' && event.chat_id === activeChatId) {
    if (streamingDraft) {
      streamingDraft.row.remove();
      streamingDraft = null;
    }
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
    if (!event.value && streamingDraft) {
      const partial = streamingDraft.content;
      streamingDraft.row.remove();
      streamingDraft = null;
      if (partial) addMessage('assistant', partial);
    }
    if (!event.value && /thinking|stop/i.test($('#status').textContent)) setStatus('Ready for your next message.');
  }
  if (event.type === 'voice_status') {
    $('#voice-status').textContent = event.message;
    const failed = event.message.startsWith('Voice error:');
    voiceActive = event.message.startsWith('Connected') || (voiceActive && !failed && event.message !== 'Voice session stopped');
    $('#voice-visualizer').classList.toggle('active', voiceActive);
    if (failed || event.message === 'Voice session stopped') voiceActive = false;
  }
  if (event.type === 'voice_text') $('#voice-reply').textContent = event.message;
  if (event.type === 'voice_status' && (event.message.startsWith('Voice error:') || event.message === 'Voice session stopped')) {
    voiceActive = false;
    $('#conversation').classList.remove('hidden');
    $('#voice-panel').classList.add('hidden');
    $('#composer-area').classList.remove('voice-mode');
    cancelAnimationFrame(waveformFrame);
    waveformFrame = 0;
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
    voiceLevels = state.voice_levels || voiceLevels;
    state.pending.forEach((item) => {
      if (item.id !== waitingForApproval?.id && !approvals.some((pending) => pending.id === item.id)) approvals.push(item);
    });
    showNextApproval();
    state.events.forEach(applyEvent);
    if (state.events.length) cursor = Math.max(cursor, ...state.events.map((event) => event.sequence));
  } catch (error) { setStatus(`Connection problem: ${error.message}`, 'error'); }
  finally { syncing = false; }
}

/** Keep event delivery push-based and fall back to polling while reconnecting. */
function connectEvents() {
  if (eventSocket && (eventSocket.readyState === WebSocket.OPEN || eventSocket.readyState === WebSocket.CONNECTING)) return;
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  eventSocket = new WebSocket(`${scheme}//${location.host}/api/events`);
  eventSocket.onopen = () => {
    clearInterval(reconnectTimer);
    reconnectTimer = null;
  };
  eventSocket.onmessage = (message) => {
    try {
      const payload = JSON.parse(message.data);
      if (payload.type === 'snapshot') {
        const state = payload.state;
        cursor = state.next_event_id || 0;
        renderHistory(state);
        setBusy(state.busy);
        preferences = state.preferences || preferences;
        applyTheme(preferences.theme);
        renderSetupWarning(state);
        voiceLevels = state.voice_levels || voiceLevels;
        state.pending.forEach((item) => {
          if (item.id !== waitingForApproval?.id && !approvals.some((pending) => pending.id === item.id)) approvals.push(item);
        });
        showNextApproval();
        return;
      }
      if (payload.sequence && payload.sequence <= cursor) return;
      applyEvent(payload);
      if (payload.sequence) cursor = payload.sequence;
      if (payload.type === 'refresh') syncState(true);
    } catch (error) { setStatus(`Live update problem: ${error.message}`, 'error'); }
  };
  eventSocket.onclose = eventSocket.onerror = () => {
    if (reconnectTimer) return;
    reconnectTimer = setInterval(() => {
      if (eventSocket?.readyState === WebSocket.CLOSED) connectEvents();
      syncState(false);
    }, 2500);
  };
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
  if (!message && !pendingAttachments.length) return;
  const attachments = [...pendingAttachments];
  request('/api/send', {message, attachments}).then(() => {
    input.value = '';
    pendingAttachments = [];
    renderAttachmentPreview();
    resizeInput();
    setStatus('Mochi is thinking…');
  }).catch((error) => setStatus(error.message, 'error'));
}

/** Upload one selected file as base64 to the local, size-limited upload endpoint. */
async function uploadAttachment(file) {
  if (file.size > 8 * 1024 * 1024) throw new Error(`${file.name} exceeds the 8 MB limit.`);
  const encoded = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
  return request('/api/upload', {name: file.name, data: encoded});
}

/** Rebuild the removable composer preview from currently queued upload records. */
function renderAttachmentPreview() {
  const preview = $('#attachment-preview');
  preview.replaceChildren();
  preview.classList.toggle('hidden', !pendingAttachments.length);
  pendingAttachments.forEach((file, index) => {
    const item = document.createElement('div');
    item.className = 'attachment-chip';
    if (file.mime_type.startsWith('image/')) {
      const image = document.createElement('img');
      image.src = file.url;
      image.alt = file.name;
      item.append(image);
    }
    const name = document.createElement('span');
    name.textContent = file.name;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `Remove ${file.name}`);
    remove.onclick = () => {
      pendingAttachments.splice(index, 1);
      renderAttachmentPreview();
    };
    item.append(name, remove);
    preview.append(item);
  });
}

$('#attach-button').onclick = () => $('#file-input').click();
$('#file-input').onchange = async (event) => {
  const files = [...event.target.files];
  event.target.value = '';
  if (pendingAttachments.length + files.length > 4) {
    setStatus('Attach up to four files per message.', 'error');
    return;
  }
  for (const file of files) {
    try {
      pendingAttachments.push(await uploadAttachment(file));
      renderAttachmentPreview();
    } catch (error) {
      setStatus(error.message, 'error');
    }
  }
};

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
    if (next && !waveformFrame) drawVoiceWaveform();
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
    cancelAnimationFrame(waveformFrame);
    waveformFrame = 0;
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
$('#theme-toggle').onclick = () => selectTheme(preferences.theme === 'Light' ? (preferences.last_dark_theme || 'Violet') : 'Light');
$('#theme-select').onchange = () => selectTheme($('#theme-select').value);
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
    connectEvents();
  } catch (error) { setStatus(`App is unavailable: ${error.message}`, 'error'); }
}
boot();
