const DEFAULT_API_URL = 'http://localhost:5187';
const PAGE_SIZE = 25;
const API_STORAGE_KEY = 'skyline.agentApiUrl';
const ui = Object.fromEntries([
  'connection-settings', 'connection-form', 'connection-fields', 'api-url', 'connection-label',
  'connection-status', 'new-session-button', 'refresh-sessions', 'sessions-status', 'session-list',
  'load-more', 'conversation-agent', 'conversation-title', 'refresh-conversation', 'new-session-form',
  'new-session-fields', 'agent-select', 'session-title', 'session-instructions', 'chat-history',
  'chat-status', 'message-form', 'message-fields', 'message', 'send-message'
].map(id => [id, document.getElementById(id)]));

const state = { apiUrl: DEFAULT_API_URL, agents: [], sessions: [], active: null, busy: false, offset: 0, hasMore: false };

function notice(element, message = '', kind = '') {
  element.textContent = message;
  element.classList.toggle('error', kind === 'error');
  element.classList.toggle('working', kind === 'working');
}

function latestRun() {
  return state.active?.runs?.at(-1);
}

function updateControls() {
  ui['connection-fields'].disabled = state.busy;
  ui['new-session-fields'].disabled = state.busy || !state.agents.length;
  ui['message-fields'].disabled = state.busy || !state.active;
  ui['send-message'].disabled = !ui.message.value.trim();
  for (const button of [ui['new-session-button'], ui['refresh-sessions'], ui['load-more'], ui['refresh-conversation'], ...ui['session-list'].querySelectorAll('button')]) {
    button.disabled = state.busy;
  }
  ui['chat-history'].setAttribute('aria-busy', String(state.busy));
}

function setBusy(busy) {
  state.busy = busy;
  updateControls();
}

async function request(path, body) {
  let response;
  try {
    response = await fetch(`${state.apiUrl}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    });
  } catch {
    throw new Error('Could not reach the API. Check the API address, that the server is running, and its CORS settings. For HTTPS, trust its development certificate.');
  }

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const validation = data?.errors ? Object.values(data.errors).flat().join(' ') : '';
    throw new Error(data?.detail || validation || data?.title || `The API replied with ${response.status} ${response.statusText}.`);
  }
  if (data === null) throw new Error('The API returned an empty or invalid JSON response.');
  return data;
}

function agentName(agentId) {
  return state.agents.find(agent => agent.id === agentId)?.name || agentId;
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
}

function renderSessions() {
  ui['session-list'].replaceChildren();
  for (const session of state.sessions) {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'session-button';
    button.setAttribute('aria-current', String(state.active?.id === session.id));
    const title = document.createElement('span');
    title.className = 'session-name';
    title.textContent = session.title || 'Untitled conversation';
    button.title = title.textContent;
    const meta = document.createElement('span');
    meta.className = 'session-meta';
    meta.textContent = `${agentName(session.agentId)} · ${formatDate(session.updatedAt)}`;
    button.append(title, meta);
    button.addEventListener('click', () => openSession(session.id));
    item.append(button);
    ui['session-list'].append(item);
  }
  ui['load-more'].hidden = !state.hasMore;
  updateControls();
}

async function loadSessions(append = false) {
  notice(ui['sessions-status'], 'Loading conversations…');
  try {
    const offset = append ? state.offset : 0;
    const sessions = await request(`/api/agent-sessions?skip=${offset}&take=${PAGE_SIZE}`);
    if (!Array.isArray(sessions)) throw new Error('The API returned an invalid conversation list.');
    state.offset = offset + sessions.length;
    state.hasMore = sessions.length === PAGE_SIZE;
    // A conversation can move between pages when its update time changes.
    state.sessions = [...new Map([...(append ? state.sessions : []), ...sessions].map(session => [session.id, session])).values()];
    renderSessions();
    notice(ui['sessions-status'], state.sessions.length ? '' : 'No conversations yet. Start your first one.');
  } catch (error) {
    notice(ui['sessions-status'], error.message, 'error');
    throw error;
  }
}

function rememberSession(session) {
  const exists = state.sessions.some(item => item.id === session.id);
  state.sessions = [session, ...state.sessions.filter(item => item.id !== session.id)]
    .sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
  if (!exists) state.offset += 1;
  renderSessions();
  notice(ui['sessions-status']);
}

function messageText(item) {
  if (typeof item.content === 'string') return item.content;
  if (!Array.isArray(item.content)) return '';
  return item.content.map(part => {
    if (part.type === 'input_text' || part.type === 'output_text' || part.type === 'text') return part.text || '';
    if (part.type === 'refusal') return part.refusal || '';
    return '';
  }).filter(Boolean).join('\n');
}

function appendMessage(role, text) {
  const message = document.createElement('article');
  message.className = `message ${role}`;
  const label = document.createElement('div');
  label.className = 'message-label';
  label.textContent = role === 'user' ? 'YOU' : agentName(state.active.agentId);
  const body = document.createElement('div');
  body.className = 'message-body';
  // Both saved history and agent replies are untrusted text, never HTML.
  body.textContent = text;
  message.append(label, body);
  ui['chat-history'].append(message);
}

function scrollChat() {
  ui['chat-history'].scrollTop = ui['chat-history'].scrollHeight;
}

function renderConversation() {
  const session = state.active;
  ui['new-session-form'].hidden = Boolean(session);
  ui['chat-history'].hidden = !session;
  ui['message-form'].hidden = !session;
  ui['refresh-conversation'].hidden = !session;
  ui['conversation-title'].textContent = session ? session.title || 'Untitled conversation' : 'Start a conversation';
  ui['conversation-agent'].textContent = session ? agentName(session.agentId) : 'AGENT CHAT';
  ui['chat-history'].replaceChildren();
  if (session) {
    for (const item of session.history || []) {
      if (item.role !== 'user' && item.role !== 'assistant') continue;
      const text = messageText(item);
      if (text) appendMessage(item.role, text);
    }
    if (!ui['chat-history'].childElementCount) {
      const empty = document.createElement('p');
      empty.className = 'chat-empty';
      empty.textContent = 'Your conversation is ready. Send a message to get started.';
      ui['chat-history'].append(empty);
    }
  }
  scrollChat();
  renderSessions();
}

function showRunStatus() {
  const run = latestRun();
  if (run?.status === 'Running') {
    // The server lock decides whether a run is active; a saved Running status
    // can survive a server restart and is recovered by the next run.
    notice(ui['chat-status'], 'The last run is marked as in progress. Refresh the chat to check for a reply.');
  } else if (run?.status === 'Failed' || run?.status === 'Cancelled') {
    notice(ui['chat-status'], run.errorMessage || `The last reply ${run.status === 'Failed' ? 'failed' : 'was cancelled'}.`, 'error');
  } else {
    notice(ui['chat-status']);
  }
}

async function openSession(id) {
  if (state.busy) return;
  setBusy(true);
  notice(ui['chat-status'], 'Loading conversation…', 'working');
  try {
    const session = await request(`/api/agent-sessions/${encodeURIComponent(id)}`);
    if (state.active?.id !== id) ui.message.value = '';
    state.active = session;
    renderConversation();
    showRunStatus();
  } catch (error) {
    notice(ui['chat-status'], error.message, 'error');
  } finally {
    setBusy(false);
  }
}

async function connect() {
  if (state.busy) return;
  let url;
  try {
    url = new URL(ui['api-url'].value.trim());
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error();
  } catch {
    notice(ui['connection-status'], 'Enter an HTTP or HTTPS base URL without credentials, a query, or a fragment.', 'error');
    return;
  }
  state.apiUrl = url.href.replace(/\/+$/, '');
  try { localStorage.setItem(API_STORAGE_KEY, state.apiUrl); } catch { /* Storage may be disabled. */ }
  setBusy(true);
  state.agents = [];
  state.sessions = [];
  state.active = null;
  state.offset = 0;
  state.hasMore = false;
  ui.message.value = '';
  ui['agent-select'].replaceChildren(new Option('Loading agents…', ''));
  renderConversation();
  notice(ui['chat-status']);
  notice(ui['connection-status']);
  ui['connection-label'].textContent = 'Connecting…';

  const [catalog, sessions] = await Promise.allSettled([request('/api/agents'), loadSessions()]);
  ui['agent-select'].replaceChildren(new Option('Choose an agent', ''));
  if (catalog.status === 'fulfilled' && Array.isArray(catalog.value)) {
    state.agents = catalog.value;
    for (const agent of state.agents) ui['agent-select'].append(new Option(agent.name, agent.id));
    if (state.agents.length) ui['agent-select'].value = state.agents[0].id;
    else notice(ui['connection-status'], 'No agents are configured. Saved conversations are still available.');
  } else {
    notice(ui['connection-status'], catalog.reason?.message || 'The API returned an invalid agent catalog.', 'error');
  }
  const connected = catalog.status === 'fulfilled' && Array.isArray(catalog.value) && sessions.status === 'fulfilled';
  ui['connection-label'].textContent = connected ? 'Connected' : 'Connection needs attention';
  if (!connected) ui['connection-settings'].open = true;
  renderSessions();
  setBusy(false);
}

ui['connection-form'].addEventListener('submit', event => {
  event.preventDefault();
  connect();
});

ui['new-session-button'].addEventListener('click', () => {
  if (state.busy) return;
  state.active = null;
  ui.message.value = '';
  renderConversation();
  notice(ui['chat-status']);
  ui['agent-select'].focus();
});

ui['new-session-form'].addEventListener('submit', async event => {
  event.preventDefault();
  if (state.busy || !ui['new-session-form'].reportValidity()) return;
  setBusy(true);
  notice(ui['chat-status'], 'Starting your conversation…', 'working');
  try {
    const session = await request('/api/agent-sessions', {
      agentId: ui['agent-select'].value,
      title: ui['session-title'].value.trim() || null,
      additionalInstructions: ui['session-instructions'].value.trim() || null
    });
    state.active = session;
    ui['session-title'].value = '';
    ui['session-instructions'].value = '';
    rememberSession(session);
    renderConversation();
    notice(ui['chat-status']);
  } catch (error) {
    notice(ui['chat-status'], error.message, 'error');
  } finally {
    setBusy(false);
    if (state.active) ui.message.focus();
  }
});

for (const [id, append] of [['refresh-sessions', false], ['load-more', true]]) {
  ui[id].addEventListener('click', async () => {
    if (state.busy) return;
    setBusy(true);
    try { await loadSessions(append); } catch { /* The sidebar displays the error. */ }
    finally { setBusy(false); }
  });
}

ui['refresh-conversation'].addEventListener('click', () => {
  if (state.active) openSession(state.active.id);
});

ui.message.addEventListener('input', updateControls);
ui.message.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!ui['message-fields'].disabled && ui.message.value.trim()) ui['message-form'].requestSubmit();
  }
});

ui['message-form'].addEventListener('submit', async event => {
  event.preventDefault();
  const message = ui.message.value.trim();
  if (state.busy || !state.active || !message || !ui['message-form'].reportValidity()) return;
  const session = state.active;
  const path = `/api/agent-sessions/${encodeURIComponent(session.id)}`;
  setBusy(true);
  ui['chat-history'].querySelector('.chat-empty')?.remove();
  appendMessage('user', message);
  scrollChat();
  notice(ui['chat-status'], 'The agent is working… A reply may take a few minutes.', 'working');
  try {
    const run = await request(`${path}/runs`, { message });
    // Keep the returned answer visible even if refreshing persisted history fails.
    session.history = [...(session.history || []), { role: 'user', content: message }];
    if (run.output) session.history.push({ role: 'assistant', content: run.output });
    session.runs = [...(session.runs || []), run];
    session.updatedAt = run.completedAt || run.startedAt;
    ui.message.value = '';
    rememberSession(session);
    renderConversation();
    showRunStatus();
    try {
      state.active = await request(path);
      renderConversation();
      showRunStatus();
    } catch {
      notice(ui['chat-status'], 'The reply was received, but saved history could not be refreshed. Use Refresh chat to try again.', 'error');
    }
  } catch (error) {
    // Failed runs can still save messages and tool activity on the server.
    // Read that history before the user decides whether to send again.
    let refreshed = false;
    try {
      state.active = await request(path);
      rememberSession(state.active);
      refreshed = true;
    } catch { /* Preserve the draft and the last confirmed history. */ }
    renderConversation();
    notice(ui['chat-status'], `${error.message} Your draft has been kept.${refreshed ? '' : ' Could not refresh saved history; refresh the chat before retrying.'}`, 'error');
  } finally {
    setBusy(false);
    ui.message.focus();
  }
});

try { ui['api-url'].value = localStorage.getItem(API_STORAGE_KEY) || DEFAULT_API_URL; } catch { /* Use the default URL. */ }
connect();
