/**
 * Wee Orchestrator WebUI — app.js
 * Vanilla ES2020 module. No build step required.
 */

// ─── Config ───────────────────────────────────────────────────────────────────
const API_BASE = '/api/v1';

// ─── State ────────────────────────────────────────────────────────────────────
const STATE = {
  token:           null,
  identity:        null,   // numeric/email identity
  channel:         null,
  username:        null,   // @handle (telegram) or null
  identityResolved: null,  // resolved numeric ID during auth
  currentSessionId: null,
  isTyping:        false,
  pendingFiles:    [],
  sessions:        [],
  activeSessionId: null,
  schedulerEnabled: true,  // overridden by /api/v1/config on boot
};

// ─── Persist ──────────────────────────────────────────────────────────────────
function saveAuth() {
  localStorage.setItem('wee_token',    STATE.token    || '');
  localStorage.setItem('wee_identity', STATE.identity || '');
  localStorage.setItem('wee_channel',  STATE.channel  || '');
  localStorage.setItem('wee_username', STATE.username || '');
}

function loadAuth() {
  STATE.token    = localStorage.getItem('wee_token')    || null;
  STATE.identity = localStorage.getItem('wee_identity') || null;
  STATE.channel  = localStorage.getItem('wee_channel')  || null;
  STATE.username = localStorage.getItem('wee_username') || null;
}

function clearAuth() {
  STATE.token = STATE.identity = STATE.channel = STATE.username = STATE.identityResolved = null;
  STATE.currentSessionId = STATE.activeSessionId = null;
  STATE.sessions = [];
  STATE.pendingFiles = [];
  ['wee_token','wee_identity','wee_channel','wee_username'].forEach(k => localStorage.removeItem(k));
}

// ─── API Layer ────────────────────────────────────────────────────────────────
async function apiRequest(method, path, body = null) {
  const headers = { 'Content-Type': 'application/json' };
  if (STATE.token) headers['Authorization'] = `Bearer ${STATE.token}`;

  const opts = { method, headers };
  if (body !== null) opts.body = JSON.stringify(body);

  const res = await fetch(`${API_BASE}${path}`, opts);

  if (res.status === 401) {
    clearAuth();
    showAuthView();
    throw new Error('Session expired. Please log in again.');
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

async function apiUpload(sessionId, file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/upload`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${STATE.token}` },
    body: form,
  });
  if (res.status === 401) { clearAuth(); showAuthView(); throw new Error('Session expired'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Upload failed: HTTP ${res.status}`);
  return data;
}

async function fetchBlob(url) {
  const res = await fetch(url, {
    headers: STATE.token ? { 'Authorization': `Bearer ${STATE.token}` } : {},
  });
  if (!res.ok) return null;
  return URL.createObjectURL(await res.blob());
}

// ─── DOM Helpers ──────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const show = el => el.classList.remove('hidden');
const hide = el => el.classList.add('hidden');

function showAuthView() {
  hide($('app'));
  show($('auth-overlay'));
  show($('auth-step1'));
  hide($('auth-step2'));
  $('auth-identity').value = '';
  $('auth-code').value = '';
  hideError('auth-error');
  hideError('auth-error2');
}

function showAppView() {
  hide($('auth-overlay'));
  show($('app'));
  updateSidebarIdentity();
  // Apply scheduler feature flag
  if (!STATE.schedulerEnabled) {
    hide($('btn-nav-scheduler'));
    hide($('scheduler-panel'));
  }
}

function updateSidebarIdentity() {
  const label = STATE.username
    ? `@${STATE.username}`
    : (STATE.identity || '—');
  $('sidebar-identity').textContent = `${STATE.channel || ''} · ${label}`;
}

function showError(id, msg) { const el = $(id); el.textContent = msg; show(el); }
function hideError(id) { hide($(id)); }

// ─── Session Meta Pills ───────────────────────────────────────────────────────
function updateSessionMeta(data) {
  const set = (id, text, extra = '') => {
    const el = $(id);
    if (!text || text === 'null' || text === 'undefined') {
      el.textContent = '—';
      el.classList.add('empty');
      el.classList.remove(extra);
    } else {
      el.textContent = text;
      el.classList.remove('empty');
      if (extra) el.classList.toggle(extra, true);
    }
  };

  set('meta-agent',   data?.agent);
  set('meta-runtime', data?.runtime);

  // Shorten model names for display
  const model = data?.model ? data.model.replace(/^claude-/, '').replace(/^gpt-/, '') : null;
  set('meta-model', model);

  const isYolo = data?.yolo_mode === 'on' || data?.yolo_mode === 'yolo';
  const modeEl = $('meta-mode');
  modeEl.textContent = isYolo ? '⚡ yolo' : 'restricted';
  modeEl.classList.toggle('yolo', isYolo);
  modeEl.classList.remove('empty');
}

async function fetchAndUpdateMeta(sessionId) {
  if (!sessionId) return;
  try {
    const data = await apiRequest('GET', `/sessions/${sessionId}/status`);
    updateSessionMeta(data);
  } catch (_) { /* non-fatal */ }
}

// ─── Auth Flow ────────────────────────────────────────────────────────────────
let _authState = 'IDLE';

async function handleRequestCode() {
  const rawIdentity = $('auth-identity').value.trim();
  const channel     = $('auth-channel').value;
  hideError('auth-error');

  if (!rawIdentity) { showError('auth-error', 'Please enter your identity.'); return; }

  $('btn-send-code').disabled = true;
  $('btn-send-code').textContent = 'Sending…';

  try {
    const data = await apiRequest('POST', '/auth/request-pairing', { identity: rawIdentity, channel });
    STATE.identityResolved = data.identity_resolved || rawIdentity.replace(/^@/, '');
    STATE.channel = channel;
    _authState = 'CODE_SENT';

    $('auth-channel-display').textContent = channel === 'telegram' ? 'Telegram' : 'WebEx';
    hide($('auth-step1'));
    show($('auth-step2'));
    $('auth-code').focus();
  } catch (err) {
    showError('auth-error', err.message);
  } finally {
    $('btn-send-code').disabled = false;
    $('btn-send-code').textContent = 'Send Pairing Code';
  }
}

async function handleVerifyCode() {
  const code = $('auth-code').value.trim();
  hideError('auth-error2');

  if (!code) { showError('auth-error2', 'Please enter the pairing code.'); return; }

  $('btn-verify-code').disabled = true;
  $('btn-verify-code').textContent = 'Verifying…';

  try {
    const data = await apiRequest('POST', '/auth/verify-pairing', {
      code,
      identity: STATE.identityResolved,
    });
    STATE.token    = data.token;
    STATE.identity = data.identity || STATE.identityResolved;
    STATE.channel  = data.channel  || STATE.channel;
    STATE.username = data.username || null;
    saveAuth();
    _authState = 'LOGGED_IN';
    showAppView();
    await initApp();
  } catch (err) {
    showError('auth-error2', err.message);
  } finally {
    $('btn-verify-code').disabled = false;
    $('btn-verify-code').textContent = 'Verify';
  }
}

// ─── Meta Pill Popovers ───────────────────────────────────────────────────────
const PILL_OPTIONS = {
  'meta-agent': {
    label: 'Switch Agent',
    options: [
      { label: '🍀 fosterbot',      cmd: '/agent set fosterbot' },
      { label: '🔧 devops',         cmd: '/agent set devops' },
      { label: '👨‍👩‍👧 family',         cmd: '/agent set family' },
      { label: '💻 opencode',       cmd: '/agent set opencode' },
      { label: '📋 list agents',    cmd: '/agent list' },
    ],
  },
  'meta-runtime': {
    label: 'Switch Runtime',
    options: [
      { label: '🟣 claude',         cmd: '/runtime set claude' },
      { label: '🐙 copilot',        cmd: '/runtime set copilot' },
      { label: '💎 gemini',         cmd: '/runtime set gemini' },
      { label: '🔓 opencode',       cmd: '/runtime set opencode' },
    ],
  },
  'meta-model': {
    label: 'Switch Model',
    options: [
      { label: 'claude-sonnet-4.5',         cmd: '/model set claude-sonnet-4.5' },
      { label: 'claude-opus-4',             cmd: '/model set claude-opus-4' },
      { label: 'claude-haiku-4.5',          cmd: '/model set claude-haiku-4.5' },
      { label: 'gpt-4o',                    cmd: '/model set gpt-4o' },
      { label: 'gpt-4o-mini',              cmd: '/model set gpt-4o-mini' },
      { label: 'gemini-1.5-pro',            cmd: '/model set gemini-1.5-pro' },
      { label: '📋 list models',            cmd: '/model list' },
    ],
  },
  'meta-mode': {
    label: 'Switch Mode',
    options: [
      { label: '⚡ yolo',           cmd: '/mode yolo' },
      { label: '🔒 restricted',     cmd: '/mode restricted' },
    ],
  },
};

let _pillPopover = null;

function hidePillPopover() {
  if (_pillPopover) { _pillPopover.remove(); _pillPopover = null; }
}

function showPillPopover(pillEl, pillId) {
  hidePillPopover();
  const config = PILL_OPTIONS[pillId];
  if (!config) return;

  const popover = document.createElement('div');
  popover.className = 'pill-popover glass-panel';

  const header = document.createElement('div');
  header.className = 'pill-popover-header';
  header.textContent = config.label;
  popover.appendChild(header);

  config.options.forEach(opt => {
    const item = document.createElement('button');
    item.className = 'pill-popover-item';
    item.textContent = opt.label;
    item.addEventListener('mousedown', e => {
      e.preventDefault();
      hidePillPopover();
      sendCommand(opt.cmd);
    });
    popover.appendChild(item);
  });

  document.body.appendChild(popover);
  _pillPopover = popover;

  // Position below the pill, flip up if needed
  const rect = pillEl.getBoundingClientRect();
  const popH = popover.offsetHeight || 200;
  let top = rect.bottom + 6;
  if (top + popH > window.innerHeight - 10) top = rect.top - popH - 6;
  let left = rect.right - popover.offsetWidth;
  if (left < 8) left = 8;
  popover.style.top  = `${top}px`;
  popover.style.left = `${left}px`;
}

async function sendCommand(cmdText) {
  const ta = $('message-input');
  ta.value = cmdText;
  autoResizeTextarea(ta);
  syncMirror();
  updateSendButton();
  await sendMessage();
}


const COMMANDS = [
  { cmd: '/agent',        usage: '/agent <set|list|current|invoke>',      desc: 'Manage agents — switch, list, or delegate' },
  { cmd: '/model',        usage: '/model <set|list|current>',              desc: 'Change the AI model' },
  { cmd: '/runtime',      usage: '/runtime <set|list|current>',            desc: 'Switch execution runtime' },
  { cmd: '/mode',         usage: '/mode <yolo|restricted|current|list>',   desc: 'Toggle yolo / restricted permission mode' },
  { cmd: '/status',       usage: '/status',                                desc: 'Show current session status' },
  { cmd: '/cancel',       usage: '/cancel',                                desc: 'Cancel a running query' },
  { cmd: '/capabilities', usage: '/capabilities',                          desc: 'List all available capabilities' },
  { cmd: '/session',      usage: '/session',                               desc: 'Show session info' },
  { cmd: '/timeout',      usage: '/timeout <seconds>',                     desc: 'Set the command timeout' },
  { cmd: '/render',       usage: '/render <text|markdown>',                desc: 'Set render output type' },
];

const SUBCOMMANDS = {
  '/agent':   [
    { sub: 'set <name>',               desc: 'Switch to a named agent (devops, family, opencode…)' },
    { sub: 'list',                     desc: 'List all available agents' },
    { sub: 'current',                  desc: 'Show the current agent' },
    { sub: 'invoke <agent> <prompt>',  desc: 'Delegate a prompt to a sub-agent' },
  ],
  '/model':   [
    { sub: 'set "<model>"',  desc: 'Switch to a specific model name' },
    { sub: 'list',           desc: 'List models for the current runtime' },
    { sub: 'current',        desc: 'Show the current model' },
  ],
  '/runtime': [
    { sub: 'set claude',    desc: 'Switch to Claude Code' },
    { sub: 'set copilot',   desc: 'Switch to GitHub Copilot CLI' },
    { sub: 'set gemini',    desc: 'Switch to Gemini' },
    { sub: 'set opencode',  desc: 'Switch to OpenCode' },
    { sub: 'list',          desc: 'List available runtimes' },
    { sub: 'current',       desc: 'Show the current runtime' },
  ],
  '/mode':    [
    { sub: 'yolo',        desc: 'Enable auto-approval (no permission prompts)' },
    { sub: 'restricted',  desc: 'Require approval for potentially destructive actions' },
    { sub: 'current',     desc: 'Show the current mode' },
    { sub: 'list',        desc: 'List available modes' },
  ],
};

// ─── Command Dropdown ─────────────────────────────────────────────────────────
let _dropActive = -1;

function getDropdownItems(text) {
  // Only trigger when text is JUST a slash-command (no other text before it)
  const trimmed = text.trimStart();
  if (!trimmed.startsWith('/')) return null;

  const parts = trimmed.split(/\s+/);
  const cmd   = parts[0].toLowerCase();  // e.g. "/age" or "/agent"
  const hasSub = parts.length > 1;       // space has been typed after cmd

  if (hasSub) {
    // Show sub-commands for exact command matches
    const subs = SUBCOMMANDS[cmd];
    if (!subs) return null;
    const subFilter = parts.slice(1).join(' ').toLowerCase();
    const filtered = subs.filter(s => s.sub.toLowerCase().startsWith(subFilter));
    if (!filtered.length) return null;
    return filtered.map(s => ({ primary: `${cmd} ${s.sub}`, name: cmd, desc: s.desc, usage: `${cmd} ${s.sub}` }));
  } else {
    // Filter top-level commands by prefix
    const filtered = COMMANDS.filter(c => c.cmd.startsWith(cmd));
    if (!filtered.length) return null;
    return filtered.map(c => ({ primary: c.cmd, name: c.cmd, desc: c.desc, usage: c.usage }));
  }
}

function showCommandDropdown(items) {
  const dd = $('cmd-dropdown');
  dd.innerHTML = '';

  items.forEach((item, idx) => {
    const row = document.createElement('div');
    row.className = 'cmd-row';
    row.setAttribute('role', 'option');
    row.dataset.idx = String(idx);
    row.innerHTML =
      `<span class="cmd-row-name">${escHtml(item.name)}</span>` +
      `<span class="cmd-row-desc">${escHtml(item.desc)}</span>` +
      `<span class="cmd-row-usage">${escHtml(item.usage)}</span>`;

    row.addEventListener('mousedown', e => {
      e.preventDefault();
      applyCompletion(item.primary);
    });
    dd.appendChild(row);
  });

  _dropActive = -1;
  show(dd);
}

function hideCommandDropdown() {
  hide($('cmd-dropdown'));
  _dropActive = -1;
}

function setDropActive(idx) {
  const rows = $('cmd-dropdown').querySelectorAll('.cmd-row');
  _dropActive = Math.max(-1, Math.min(idx, rows.length - 1));
  rows.forEach((r, i) => r.classList.toggle('active', i === _dropActive));
  if (_dropActive >= 0) rows[_dropActive].scrollIntoView({ block: 'nearest' });
}

function applyCompletion(fullCmd) {
  const ta = $('message-input');
  // Replace text from the start up to the end with the completion + space
  const trimmed = ta.value.trimStart();
  const leadingWS = ta.value.slice(0, ta.value.length - trimmed.length);
  const parts = trimmed.split(/\s+/);
  // Keep everything after the command words already typed (e.g. "/agent set " keeps nothing to replace)
  const newText = leadingWS + fullCmd + ' ';
  ta.value = newText;
  ta.focus();
  // Cursor to end
  ta.selectionStart = ta.selectionEnd = newText.length;
  syncMirror();
  updateSendButton();
  hideCommandDropdown();
}

// ─── Mirror Sync ──────────────────────────────────────────────────────────────
function syncMirror() {
  const ta     = $('message-input');
  const mirror = $('input-mirror');

  const text = ta.value;
  const esc  = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Highlight /command tokens — only when they appear at start or after whitespace
  const highlighted = esc.replace(
    /((?:^|[ \t\n]))(\/\w+)/g,
    (_, prefix, token) => `${prefix}<span class="cmd-token">${token}</span>`
  );

  // Trailing zero-width space forces the div to match textarea height on last empty line
  mirror.innerHTML = highlighted + '\u200b';
  mirror.scrollTop = ta.scrollTop;

  // Update dropdown
  const trimmed = text.trimStart();
  if (trimmed.startsWith('/')) {
    const items = getDropdownItems(trimmed);
    if (items) {
      showCommandDropdown(items);
    } else {
      hideCommandDropdown();
    }
  } else {
    hideCommandDropdown();
  }
}

// ─── Session Management ───────────────────────────────────────────────────────
async function loadSessions() {
  try {
    const data = await apiRequest('GET', '/history/sessions');
    STATE.sessions = data.sessions || [];
    renderSessionList();
  } catch (_) {
    STATE.sessions = [];
  }
}

function renderSessionList() {
  const list = $('sessions-list');
  list.innerHTML = '';

  if (STATE.sessions.length === 0) {
    list.innerHTML = '<p style="color:var(--text-muted);font-size:12px;padding:8px 12px;">No saved sessions yet.</p>';
    return;
  }

  for (const s of STATE.sessions) {
    const item = document.createElement('div');
    item.className = 'session-item' + (s.session_id === STATE.activeSessionId ? ' active' : '');
    item.dataset.sessionId = s.session_id;

    const title   = s.title   || s.session_id;
    const preview = s.preview || '';

    item.innerHTML =
      `<div class="session-title">${escHtml(title)}</div>` +
      `<div class="session-preview">${escHtml(preview)}</div>` +
      `<button class="session-delete-btn" data-id="${escHtml(s.session_id)}" title="Delete">✕</button>`;

    item.addEventListener('click', e => {
      if (e.target.classList.contains('session-delete-btn')) return;
      selectSession(s.session_id);
    });
    item.querySelector('.session-delete-btn').addEventListener('click', e => {
      e.stopPropagation();
      deleteSession(s.session_id);
    });
    list.appendChild(item);
  }
}

async function selectSession(sessionId) {
  STATE.activeSessionId  = sessionId;
  STATE.currentSessionId = sessionId;
  $('header-session-id').textContent = sessionId;

  document.querySelectorAll('.session-item').forEach(el =>
    el.classList.toggle('active', el.dataset.sessionId === sessionId)
  );

  clearMessages();
  try {
    const data = await apiRequest('GET', `/history/sessions/${sessionId}/messages`);
    for (const msg of (data.messages || [])) {
      await renderMessage(msg.role, msg.content, msg.files || []);
    }
  } catch (err) {
    renderSystemMessage('Could not load messages: ' + err.message);
  }
  scrollToBottom();
  await fetchAndUpdateMeta(sessionId);
}

async function startNewSession() {
  try {
    const data = await apiRequest('POST', '/sessions/create', {});
    STATE.currentSessionId = data.session_id;
    STATE.activeSessionId  = data.session_id;
    $('header-session-id').textContent = data.session_id;
    clearMessages();
    hide($('empty-state'));
    updateSessionMeta(data);  // initial meta from create response
    await loadSessions();
    await fetchAndUpdateMeta(data.session_id); // get full defaults
  } catch (err) {
    alert('Failed to create session: ' + err.message);
  }
}

async function deleteSession(sessionId) {
  if (!confirm('Delete this session from history?')) return;
  try {
    await apiRequest('DELETE', `/history/sessions/${sessionId}`);
    STATE.sessions = STATE.sessions.filter(s => s.session_id !== sessionId);
    renderSessionList();
    if (STATE.currentSessionId === sessionId) {
      STATE.currentSessionId = null;
      STATE.activeSessionId  = null;
      $('header-session-id').textContent = '—';
      clearMessages();
      show($('empty-state'));
      updateSessionMeta(null);
    }
  } catch (err) {
    alert('Could not delete: ' + err.message);
  }
}

// ─── Messaging ────────────────────────────────────────────────────────────────
async function sendMessage() {
  if (STATE.isTyping) return;

  const textarea = $('message-input');
  let query = textarea.value.trim();
  if (!query && STATE.pendingFiles.length === 0) return;

  if (!STATE.currentSessionId) {
    await startNewSession();
  }

  const fileRefs = STATE.pendingFiles.map(f => f.file_path);
  if (fileRefs.length) {
    query += '\n\nFiles attached:\n' + fileRefs.join('\n');
  }

  const fileNames = STATE.pendingFiles.map(f => f.filename);

  await renderMessage('user', query, fileNames);

  textarea.value = '';
  autoResizeTextarea(textarea);
  syncMirror();
  clearPendingFiles();
  $('btn-send').disabled = true;
  hideCommandDropdown();

  showTyping();
  try {
    const result = await sendMessageStreaming(query, STATE.currentSessionId);
    hideTyping();
    // Refresh meta — a /agent set etc. may have changed things
    await fetchAndUpdateMeta(STATE.currentSessionId);
    await loadSessions();
  } catch (err) {
    hideTyping();
    renderSystemMessage('Error: ' + err.message);
  } finally {
    $('btn-send').disabled = false;
    scrollToBottom();
  }
}

/**
 * Send a message using the SSE /stream endpoint and update the UI live.
 * Returns the `done` event payload {response, runtime, model} on success.
 * Throws on network/HTTP error so the caller can fall back gracefully.
 */
async function sendMessageStreaming(query, sessionId) {
  const headers = { 'Content-Type': 'application/json' };
  if (STATE.token) headers['Authorization'] = `Bearer ${STATE.token}`;

  const res = await fetch(`${API_BASE}/sessions/${sessionId}/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ query }),
  });

  if (!res.ok) throw new Error(`Stream request failed: HTTP ${res.status}`);

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer    = '';

  // Placeholder bubble that we fill in progressively
  let streamRow    = null;
  let streamBubble = null;
  let rawText      = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by '\n\n'; split and keep incomplete tail
      const frames = buffer.split('\n\n');
      buffer = frames.pop();  // last entry may be incomplete

      for (const frame of frames) {
        for (const line of frame.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6).trim();
          if (!payload) continue;

          let evt;
          try { evt = JSON.parse(payload); } catch { continue; }

          if (evt.type === 'start') {
            // Create the live streaming bubble
            hideTyping();
            ({ row: streamRow, bubble: streamBubble } = createStreamingBubble());

          } else if (evt.type === 'chunk' && streamBubble) {
            rawText += evt.text;
            // Show formatted text while streaming so it feels instant
            streamBubble.classList.add('streaming');
            // Format streaming text with intelligent line breaks
            let formatted = rawText
              .replace(/</g, '&lt;')
              .replace(/>/g, '&gt;')
              .replace(/\n\n+/g, '\n\n')  // normalize paragraph breaks
              .replace(/:\s*(?=[A-Z])/g, ':\n')  // colon followed by capital letter
              .replace(/:\s+/g, ':\n')    // colon followed by space
              .replace(/\.\s*(?=[A-Z])/g, '.\n')  // period followed by capital letter
              .replace(/\.\s+/g, '.\n')   // period followed by space
              .replace(/\?\s+/g, '?\n')   // questions
              .replace(/\!\s+/g, '!\n')   // exclamations
              .replace(/\n\n+/g, '</p><p>')  // paragraph breaks
              .replace(/\n/g, '<br>');    // line breaks
            streamBubble.innerHTML = formatted ? `<p>${formatted}</p>` : '';
            scrollToBottom();

          } else if (evt.type === 'done') {
            // Replace raw text with fully-rendered markdown
            if (streamBubble) {
              streamBubble.classList.remove('streaming');
              applyMarkdownToBubble(streamBubble, evt.response || '(no response)');
              scrollToBottom();
            } else {
              // Command/no-chunk path: render fresh bubble
              await renderMessage('assistant', evt.response || '(no response)', []);
            }
            return evt;  // caller can read runtime/model

          } else if (evt.type === 'error') {
            if (streamBubble) streamBubble.remove();
            if (streamRow)    streamRow.remove();
            throw new Error(evt.message || 'Stream error');
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
  return null;
}

/** Inject markdown+highlight into an existing bubble element. */
function applyMarkdownToBubble(bubble, content) {
  try {
    bubble.innerHTML = marked.parse(content);
    bubble.querySelectorAll('pre code').forEach(block => {
      if (window.hljs) hljs.highlightElement(block);
    });
  } catch (_) {
    bubble.textContent = content;
  }
}

/**
 * Create an empty assistant message row appended to the messages container.
 * Returns { row, bubble } so the caller can update or remove them.
 */
function createStreamingBubble() {
  hide($('empty-state'));
  const container = $('messages');

  const row = document.createElement('div');
  row.className = 'message-row assistant';

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = '🍀';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble streaming';

  row.appendChild(avatar);
  row.appendChild(bubble);
  container.appendChild(row);
  scrollToBottom();
  return { row, bubble };
}

// ─── Render Messages ──────────────────────────────────────────────────────────
function clearMessages() {
  const container = $('messages');
  container.innerHTML = '';
  const es = document.createElement('div');
  es.id = 'empty-state';
  es.className = 'empty-state hidden';
  es.innerHTML = '<div class="empty-icon">🍀</div><p>Start a conversation or select a session from the sidebar.</p>';
  container.appendChild(es);
}

async function renderMessage(role, content, files = []) {
  hide($('empty-state'));

  const container = $('messages');
  const row = document.createElement('div');
  row.className = `message-row ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = role === 'user' ? '👤' : '🍀';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';

  if (role === 'user') {
    // Highlight /command tokens in user bubbles too
    const esc = escHtml(content);
    const highlighted = esc.replace(
      /((?:^|[ \t\n]))(\/\w+)/g,
      (_, prefix, token) => `${prefix}<span class="cmd-token">${token}</span>`
    );
    bubble.innerHTML = `<span style="white-space:pre-wrap">${highlighted}</span>`;
  } else {
    try {
      bubble.innerHTML = marked.parse(content);
      bubble.querySelectorAll('pre code').forEach(block => {
        if (window.hljs) hljs.highlightElement(block);
      });
    } catch (_) {
      bubble.textContent = content;
    }
  }

  for (const fname of files) {
    if (/\.(png|jpe?g|gif|webp|svg)$/i.test(fname)) {
      const url      = `${API_BASE}/uploads/${STATE.currentSessionId}/${encodeURIComponent(fname)}`;
      const blobUrl  = await fetchBlob(url).catch(() => null);
      if (blobUrl) {
        const img      = document.createElement('img');
        img.src        = blobUrl;
        img.className  = 'message-image';
        img.alt        = fname;
        img.title      = fname;
        bubble.appendChild(img);
      }
    }
  }

  row.appendChild(avatar);
  row.appendChild(bubble);
  container.appendChild(row);
  scrollToBottom();
}

function renderSystemMessage(text) {
  const container = $('messages');
  const el = document.createElement('div');
  el.style.cssText = 'text-align:center;color:var(--danger);font-size:13px;padding:8px;';
  el.textContent = text;
  container.appendChild(el);
  scrollToBottom();
}

function scrollToBottom() {
  const c = $('messages');
  c.scrollTop = c.scrollHeight;
}

// ─── Typing Indicator ─────────────────────────────────────────────────────────
function showTyping() { STATE.isTyping = true;  show($('typing-indicator')); scrollToBottom(); }
function hideTyping() { STATE.isTyping = false; hide($('typing-indicator')); }

// ─── File Uploads ─────────────────────────────────────────────────────────────
async function handleFileSelect(file) {
  if (!STATE.currentSessionId) await startNewSession();
  try {
    const data = await apiUpload(STATE.currentSessionId, file);
    STATE.pendingFiles.push({ filename: data.filename, file_path: data.file_path, mime_type: data.mime_type });
    renderFilePreviews();
  } catch (err) {
    alert('Upload failed: ' + err.message);
  }
}

function renderFilePreviews() {
  const strip = $('file-preview-strip');
  if (!STATE.pendingFiles.length) { hide(strip); strip.innerHTML = ''; return; }
  show(strip);
  strip.innerHTML = '';
  STATE.pendingFiles.forEach((f, idx) => {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.innerHTML = `📎 ${escHtml(f.filename)} <button class="file-chip-remove" data-idx="${idx}">✕</button>`;
    chip.querySelector('.file-chip-remove').addEventListener('click', () => {
      STATE.pendingFiles.splice(idx, 1);
      renderFilePreviews();
    });
    strip.appendChild(chip);
  });
  updateSendButton();
}

function clearPendingFiles() { STATE.pendingFiles = []; renderFilePreviews(); }

// ─── Input Helpers ────────────────────────────────────────────────────────────
function autoResizeTextarea(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 180) + 'px';
}

function updateSendButton() {
  $('btn-send').disabled = !$('message-input').value.trim() && !STATE.pendingFiles.length;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ─── Sidebar Toggle ───────────────────────────────────────────────────────────
function toggleSidebar(open) {
  const sidebar = document.querySelector('.sidebar');
  const openBtn  = $('btn-open-sidebar');
  const closeBtn = $('btn-sidebar-toggle');
  if (open === undefined) open = sidebar.classList.contains('collapsed');

  if (open) {
    sidebar.classList.remove('collapsed');
    hide(openBtn);
    show(closeBtn);
  } else {
    sidebar.classList.add('collapsed');
    show(openBtn);
    hide(closeBtn);
  }
}

// ─── Channel UX ───────────────────────────────────────────────────────────────
function updateChannelUX() {
  const channel = $('auth-channel').value;
  const label   = $('identity-label');
  const input   = $('auth-identity');
  if (channel === 'webex') {
    label.textContent = 'Email address';
    input.placeholder = 'user@example.com';
    input.type = 'email';
  } else {
    label.textContent = 'Telegram @username';
    input.placeholder = '@yourname';
    input.type = 'text';
  }
}

// ─── Init ─────────────────────────────────────────────────────────────────────
async function initApp() {
  $('header-session-id').textContent = '—';
  updateSessionMeta(null);
  STATE.currentSessionId = null;
  STATE.activeSessionId  = null;
  updateSidebarIdentity();
  await loadSessions();
  if (STATE.sessions.length === 0) {
    await startNewSession();
  } else {
    show($('empty-state'));
  }
}

// ─── Boot ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {

  // --- Dev Detection ---
  const isDev = window.location.port === '8001';
  if (isDev) {
    const sidebarHeader = document.querySelector('.sidebar-header');
    if (sidebarHeader) {
      // Create orange pulsing DEV pill
      const devBadge = document.createElement('span');
      devBadge.className = 'dev-badge';
      devBadge.innerHTML = '🔧 DEV';
      sidebarHeader.appendChild(devBadge);
    }
  }

  // --- Auth ---
  $('auth-channel').addEventListener('change', updateChannelUX);
  updateChannelUX();

  $('btn-send-code').addEventListener('click', handleRequestCode);
  $('auth-identity').addEventListener('keydown', e => { if (e.key === 'Enter') handleRequestCode(); });
  $('btn-verify-code').addEventListener('click', handleVerifyCode);
  $('auth-code').addEventListener('keydown', e => { if (e.key === 'Enter') handleVerifyCode(); });
  $('btn-back').addEventListener('click', () => {
    hide($('auth-step2'));
    show($('auth-step1'));
    _authState = 'IDLE';
  });

  // --- Sidebar ---
  $('btn-new-chat').addEventListener('click', startNewSession);
  $('btn-logout').addEventListener('click', () => { clearAuth(); showAuthView(); });
  $('btn-sidebar-toggle').addEventListener('click', () => toggleSidebar(false));
  $('btn-open-sidebar').addEventListener('click',  () => toggleSidebar(true));
  $('btn-sched-open-sidebar').addEventListener('click', () => toggleSidebar(true));

  // --- View nav ---
  $('btn-nav-chat').addEventListener('click', showChatPanel);
  $('btn-nav-scheduler').addEventListener('click', showSchedulerPanel);

  // --- Send ---
  $('btn-send').addEventListener('click', sendMessage);

  // --- Textarea ---
  const ta = $('message-input');

  ta.addEventListener('input', () => {
    autoResizeTextarea(ta);
    updateSendButton();
    syncMirror();
  });

  ta.addEventListener('scroll', () => {
    $('input-mirror').scrollTop = ta.scrollTop;
  });

  ta.addEventListener('keydown', e => {
    const dd = $('cmd-dropdown');
    const ddVisible = !dd.classList.contains('hidden');

    if (ddVisible) {
      const rows = dd.querySelectorAll('.cmd-row');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setDropActive(_dropActive + 1);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setDropActive(_dropActive - 1);
        return;
      }
      if ((e.key === 'Tab' || e.key === 'Enter') && _dropActive >= 0) {
        e.preventDefault();
        const cmd = rows[_dropActive].querySelector('.cmd-row-name').textContent;
        // Find the full completion for this row
        const items = getDropdownItems(ta.value.trimStart());
        if (items && items[_dropActive]) applyCompletion(items[_dropActive].primary);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        hideCommandDropdown();
        return;
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  ta.addEventListener('blur', () => {
    // Small delay so mousedown on dropdown fires first
    setTimeout(() => hideCommandDropdown(), 150);
  });

  // --- File input ---
  $('file-input').addEventListener('change', e => {
    const file = e.target.files[0];
    if (file) handleFileSelect(file);
    e.target.value = '';
  });

  $('messages').addEventListener('dragover', e => e.preventDefault());
  $('messages').addEventListener('drop', e => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) handleFileSelect(file);
  });

  // --- Meta pill popovers ---
  ['meta-agent', 'meta-runtime', 'meta-model', 'meta-mode'].forEach(id => {
    $(id).addEventListener('click', e => { e.stopPropagation(); showPillPopover($(id), id); });
  });
  document.addEventListener('mousedown', e => {
    if (_pillPopover && !_pillPopover.contains(e.target)) hidePillPopover();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hidePillPopover(); });

  // --- Scheduler UI events ---
  $('btn-sched-refresh').addEventListener('click', () => loadSchedulerJobs(true));
  $('btn-sched-new').addEventListener('click', openNewJobForm);
  $('btn-sched-detail-close').addEventListener('click', closeSchedDetail);

  // --- Bootstrap ---
  // Fetch feature flags first (no auth needed) then decide what to show
  fetch('/api/v1/config')
    .then(r => r.json())
    .then(cfg => { STATE.schedulerEnabled = cfg.scheduler_enabled !== false; })
    .catch(() => { /* keep default true */ })
    .finally(() => {
      loadAuth();
      if (STATE.token) {
        showAppView();
        initApp();
      } else {
        showAuthView();
      }
    });
});

// ═══════════════════════════════════════════════════════════════════════════════
// ─── View Switching ──────────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

function showChatPanel() {
  show($('chat-panel'));
  hide($('scheduler-panel'));
  show($('btn-new-chat'));
  show($('sessions-list'));
  $('btn-nav-chat').classList.add('active');
  $('btn-nav-scheduler').classList.remove('active');
}

function showSchedulerPanel() {
  hide($('chat-panel'));
  show($('scheduler-panel'));
  hide($('btn-new-chat'));
  hide($('sessions-list'));
  $('btn-nav-scheduler').classList.add('active');
  $('btn-nav-chat').classList.remove('active');
  loadSchedulerJobs();
  loadSchedulerStatus();
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Scheduler API Layer ─────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

async function schedApi(method, path, body = null) {
  return apiRequest(method, `/scheduler${path}`, body);
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Scheduler State ─────────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

const SCHED = {
  jobs: [],
  selectedJobId: null,
  isLoadingJobs: false,
};

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Scheduler: Load & Render Job List ───────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

async function loadSchedulerJobs(showToast = false) {
  if (SCHED.isLoadingJobs) return;
  SCHED.isLoadingJobs = true;
  const list = $('sched-jobs-list');
  list.innerHTML = '<p class="sched-empty">Loading…</p>';
  try {
    const data = await schedApi('GET', '/jobs');
    SCHED.jobs = data.result || [];
    renderSchedulerJobs();
    if (showToast) schedToast('Jobs refreshed', 'success');
  } catch (err) {
    list.innerHTML = `<p class="sched-empty sched-error">Failed to load jobs: ${escHtml(err.message)}</p>`;
  } finally {
    SCHED.isLoadingJobs = false;
  }
}

async function loadSchedulerStatus() {
  const badge = $('sched-daemon-badge');
  try {
    const data = await schedApi('GET', '/status');
    const info = data.result || {};
    if (info.executor_running) {
      badge.textContent = '● running';
      badge.className = 'sched-daemon-badge badge-ok';
      badge.title = `Executor running · ${info.jobs_count} job(s), ${info.enabled_count} enabled`;
    } else {
      badge.textContent = '● stopped';
      badge.className = 'sched-daemon-badge badge-warn';
      badge.title = 'task-scheduler-executor.service is not running';
    }
  } catch (_) {
    badge.textContent = '● unknown';
    badge.className = 'sched-daemon-badge badge-muted';
    badge.title = 'Could not check executor status';
  }
}

function renderSchedulerJobs() {
  const list = $('sched-jobs-list');
  if (!SCHED.jobs.length) {
    list.innerHTML = '<p class="sched-empty">No scheduled jobs. Click <strong>+ New Job</strong> to create one.</p>';
    return;
  }

  list.innerHTML = '';
  for (const job of SCHED.jobs) {
    const card = document.createElement('div');
    card.className = 'sched-job-card' + (job.id === SCHED.selectedJobId ? ' selected' : '');
    card.dataset.jobId = job.id;

    const statusClass = job.enabled ? 'status-enabled' : 'status-disabled';
    const statusLabel = job.enabled ? 'enabled' : 'paused';
    const nextRun = job.next_run ? fmtDate(job.next_run) : '—';
    const lastRun = job.last_run ? fmtDate(job.last_run) : 'never';

    card.innerHTML = `
      <div class="sched-job-top">
        <span class="sched-job-name">${escHtml(job.name)}</span>
        <span class="sched-job-status ${statusClass}">${statusLabel}</span>
      </div>
      <div class="sched-job-meta">
        <span title="Schedule">⏰ ${escHtml(job.schedule)}</span>
        <span title="Agent / Runtime">🤖 ${escHtml(job.agent)} · ${escHtml(job.runtime)}</span>
      </div>
      <div class="sched-job-times">
        <span title="Next run">Next: ${escHtml(nextRun)}</span>
        <span title="Last run">Last: ${escHtml(lastRun)}</span>
      </div>
    `;

    card.addEventListener('click', () => openJobDetail(job.id));
    list.appendChild(card);
  }
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch (_) { return iso; }
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Scheduler: Detail Panel ─────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

function closeSchedDetail() {
  SCHED.selectedJobId = null;
  hide($('sched-detail'));
  document.querySelectorAll('.sched-job-card').forEach(c => c.classList.remove('selected'));
}

async function openJobDetail(jobId) {
  SCHED.selectedJobId = jobId;
  document.querySelectorAll('.sched-job-card').forEach(c =>
    c.classList.toggle('selected', c.dataset.jobId === jobId)
  );

  const job = SCHED.jobs.find(j => j.id === jobId);
  if (!job) return;

  $('sched-detail-title').textContent = job.name;
  const body = $('sched-detail-body');
  body.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">Loading…</p>';
  show($('sched-detail'));

  renderJobDetailView(job);
}

function renderJobDetailView(job) {
  const body = $('sched-detail-body');
  const isEnabled = job.enabled;

  body.innerHTML = `
    <div class="sched-detail-tabs">
      <button class="sched-tab active" data-tab="info">Info</button>
      <button class="sched-tab" data-tab="edit">Edit</button>
      <button class="sched-tab" data-tab="results">Results</button>
    </div>
    <div id="sched-tab-info" class="sched-tab-pane">
      <dl class="sched-dl">
        <dt>ID</dt>       <dd><code>${escHtml(job.id)}</code></dd>
        <dt>Schedule</dt> <dd>${escHtml(job.schedule)}</dd>
        <dt>Agent</dt>    <dd>${escHtml(job.agent)}</dd>
        <dt>Runtime</dt>  <dd>${escHtml(job.runtime)}</dd>
        <dt>Recurring</dt><dd>${job.recurring ? 'Yes' : 'No (one-shot)'}</dd>
        <dt>Notify</dt>   <dd>${job.notify ? 'Yes (Telegram)' : 'No'}</dd>
        <dt>Next run</dt> <dd>${escHtml(job.next_run ? fmtDate(job.next_run) : '—')}</dd>
        <dt>Last run</dt> <dd>${escHtml(job.last_run ? fmtDate(job.last_run) : 'never')}</dd>
        <dt>Created</dt>  <dd>${escHtml(job.created_at ? fmtDate(job.created_at) : '—')}</dd>
      </dl>
      <div class="sched-task-box">
        <label>Task prompt</label>
        <pre class="sched-task-pre">${escHtml(job.task || '(empty)')}</pre>
      </div>
      <div class="sched-detail-actions">
        ${isEnabled
          ? `<button class="btn btn-ghost btn-sm" id="btn-job-pause">⏸ Pause</button>`
          : `<button class="btn btn-primary btn-sm" id="btn-job-resume">▶ Resume</button>`
        }
        <button class="btn btn-danger btn-sm" id="btn-job-delete">🗑 Delete</button>
      </div>
    </div>
    <div id="sched-tab-edit" class="sched-tab-pane hidden"></div>
    <div id="sched-tab-results" class="sched-tab-pane hidden"></div>
  `;

  // Tab switching
  body.querySelectorAll('.sched-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      body.querySelectorAll('.sched-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      body.querySelectorAll('.sched-tab-pane').forEach(p => hide(p));
      const pane = $(`sched-tab-${tab.dataset.tab}`);
      show(pane);
      if (tab.dataset.tab === 'edit' && !pane.children.length) renderJobEditForm(job, pane);
      if (tab.dataset.tab === 'results') loadJobResults(job.id, pane);
    });
  });

  // Action buttons
  const pauseBtn   = body.querySelector('#btn-job-pause');
  const resumeBtn  = body.querySelector('#btn-job-resume');
  const deleteBtn  = body.querySelector('#btn-job-delete');

  if (pauseBtn)  pauseBtn.addEventListener('click',  () => doJobPause(job.id));
  if (resumeBtn) resumeBtn.addEventListener('click', () => doJobResume(job.id));
  if (deleteBtn) deleteBtn.addEventListener('click', () => doJobDelete(job.id));
}

function renderJobEditForm(job, container) {
  container.innerHTML = buildJobForm(job);
  wireJobForm(container, async (payload) => {
    try {
      await schedApi('PUT', `/jobs/${job.id}`, payload);
      schedToast('Job updated', 'success');
      await loadSchedulerJobs();
      const updated = SCHED.jobs.find(j => j.id === job.id);
      if (updated) {
        $('sched-detail-title').textContent = updated.name;
        renderJobDetailView(updated);
      }
    } catch (err) {
      schedToast('Update failed: ' + err.message, 'error');
    }
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Scheduler: New Job Form ─────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

function openNewJobForm() {
  SCHED.selectedJobId = null;
  document.querySelectorAll('.sched-job-card').forEach(c => c.classList.remove('selected'));
  $('sched-detail-title').textContent = 'New Scheduled Job';
  const body = $('sched-detail-body');
  body.innerHTML = buildJobForm(null);
  wireJobForm(body, async (payload) => {
    try {
      await schedApi('POST', '/jobs', payload);
      schedToast('Job created', 'success');
      closeSchedDetail();
      await loadSchedulerJobs();
    } catch (err) {
      schedToast('Create failed: ' + err.message, 'error');
    }
  });
  show($('sched-detail'));
}

function buildJobForm(job) {
  const v = (field, fallback = '') => escHtml(job?.[field] ?? fallback);
  const checked = (field, fallback = false) => (job?.[field] ?? fallback) ? 'checked' : '';
  return `
    <form class="sched-form" id="sched-job-form">
      <div class="form-group">
        <label>Name <span class="req">*</span></label>
        <input class="glass-input" name="name" value="${v('name')}" placeholder="Daily summary" required />
      </div>
      <div class="form-group">
        <label>Schedule <span class="req">*</span></label>
        <input class="glass-input" name="schedule" value="${v('schedule')}" placeholder="every day at 9am" required />
        <p class="form-hint">e.g. "in 5 minutes", "every day at 9am", "every Monday at 8am", "every 6 hours"</p>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Agent</label>
          <input class="glass-input" name="agent" value="${v('agent', 'fosterbot')}" placeholder="fosterbot" />
        </div>
        <div class="form-group">
          <label>Runtime</label>
          <select class="glass-input glass-select" name="runtime">
            <option value="claude"   ${(job?.runtime ?? 'claude') === 'claude'   ? 'selected' : ''}>claude</option>
            <option value="copilot"  ${job?.runtime === 'copilot'  ? 'selected' : ''}>copilot</option>
            <option value="gemini"   ${job?.runtime === 'gemini'   ? 'selected' : ''}>gemini</option>
            <option value="opencode" ${job?.runtime === 'opencode' ? 'selected' : ''}>opencode</option>
          </select>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Model <small class="form-hint-inline">(optional — leave blank for runtime default)</small></label>
          <input class="glass-input" name="model" value="${v('model')}" placeholder="e.g. sonnet, gpt-4o, gemini-1.5-pro" />
        </div>
        <div class="form-group">
          <label>Mode</label>
          <select class="glass-input glass-select" name="mode">
            <option value="restricted" ${(job?.mode ?? 'restricted') === 'restricted' ? 'selected' : ''}>restricted (safe)</option>
            <option value="yolo"       ${job?.mode === 'yolo' ? 'selected' : ''}>yolo (auto-approve)</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Task prompt <span class="req">*</span></label>
        <textarea class="glass-input sched-task-input" name="task" rows="4" placeholder="Describe the task the agent should perform…" required>${v('task')}</textarea>
      </div>
      <div class="form-checks">
        <label class="form-check">
          <input type="checkbox" name="recurring" ${checked('recurring', true)} />
          <span>Recurring</span>
          <small>Uncheck for one-shot execution</small>
        </label>
        <label class="form-check">
          <input type="checkbox" name="notify" ${checked('notify', false)} />
          <span>Telegram notify</span>
          <small>Send result via Telegram when complete</small>
        </label>
      </div>
      <div class="sched-form-actions">
        <button type="submit" class="btn btn-primary">💾 Save</button>
        <button type="button" class="btn btn-ghost" id="btn-form-cancel">Cancel</button>
      </div>
      <p id="sched-form-error" class="auth-error hidden"></p>
    </form>
  `;
}

function wireJobForm(container, onSubmit) {
  const form = container.querySelector('#sched-job-form');
  const errEl = container.querySelector('#sched-form-error');
  const cancelBtn = container.querySelector('#btn-form-cancel');

  if (cancelBtn) cancelBtn.addEventListener('click', closeSchedDetail);

  form.addEventListener('submit', async e => {
    e.preventDefault();
    errEl.classList.add('hidden');
    const data = Object.fromEntries(new FormData(form));
    if (!data.name?.trim())     { showFormErr(errEl, 'Name is required'); return; }
    if (!data.schedule?.trim()) { showFormErr(errEl, 'Schedule is required'); return; }
    if (!data.task?.trim())     { showFormErr(errEl, 'Task prompt is required'); return; }

    const payload = {
      name:      data.name.trim(),
      schedule:  data.schedule.trim(),
      agent:     data.agent?.trim() || 'fosterbot',
      runtime:   data.runtime || 'claude',
      model:     data.model?.trim() || null,
      mode:      data.mode || 'restricted',
      task:      data.task.trim(),
      recurring: !!data.recurring,
      notify:    !!data.notify,
    };

    const submitBtn = form.querySelector('[type=submit]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving…';
    try {
      await onSubmit(payload);
    } catch (_) {
      // Error handled by caller
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = '💾 Save';
    }
  });
}

function showFormErr(el, msg) {
  el.textContent = msg;
  el.classList.remove('hidden');
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Scheduler: Job Actions ──────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

async function doJobPause(jobId) {
  try {
    await schedApi('POST', `/jobs/${jobId}/pause`);
    schedToast('Job paused', 'success');
    await loadSchedulerJobs();
    const updated = SCHED.jobs.find(j => j.id === jobId);
    if (updated) renderJobDetailView(updated);
  } catch (err) {
    schedToast('Failed: ' + err.message, 'error');
  }
}

async function doJobResume(jobId) {
  try {
    await schedApi('POST', `/jobs/${jobId}/resume`);
    schedToast('Job resumed', 'success');
    await loadSchedulerJobs();
    const updated = SCHED.jobs.find(j => j.id === jobId);
    if (updated) renderJobDetailView(updated);
  } catch (err) {
    schedToast('Failed: ' + err.message, 'error');
  }
}

async function doJobDelete(jobId) {
  if (!confirm('Delete this scheduled job? This cannot be undone.')) return;
  try {
    await schedApi('DELETE', `/jobs/${jobId}`);
    schedToast('Job deleted', 'success');
    closeSchedDetail();
    await loadSchedulerJobs();
  } catch (err) {
    schedToast('Failed: ' + err.message, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Scheduler: Results View ─────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

async function loadJobResults(jobId, container) {
  container.innerHTML = '<p style="color:var(--text-muted);font-size:13px;padding:8px 0;">Loading results…</p>';
  try {
    const data = await schedApi('GET', `/jobs/${jobId}/results?limit=20`);
    const results = data.result || [];
    if (!results.length) {
      container.innerHTML = '<p class="sched-empty">No execution results yet.</p>';
      return;
    }
    container.innerHTML = results.map(r => `
      <div class="sched-result-card ${r.success ? 'result-ok' : 'result-fail'}">
        <div class="sched-result-header">
          <span class="result-icon">${r.success ? '✓' : '✗'}</span>
          <span class="result-ts">${escHtml(fmtDate(r.timestamp))}</span>
        </div>
        ${r.output ? `<pre class="result-output">${escHtml(r.output.slice(0, 1000))}${r.output.length > 1000 ? '\n…(truncated)' : ''}</pre>` : ''}
        ${r.error  ? `<pre class="result-error">${escHtml(r.error.slice(0, 500))}</pre>` : ''}
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<p class="sched-empty sched-error">Failed to load results: ${escHtml(err.message)}</p>`;
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Scheduler: Toast Notifications ─────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

let _toastTimer = null;

function schedToast(msg, type = 'info') {
  let toast = $('sched-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'sched-toast';
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.className = `sched-toast sched-toast-${type}`;
  toast.style.opacity = '1';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { toast.style.opacity = '0'; }, 2500);
}
