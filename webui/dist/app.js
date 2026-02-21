/**
 * Wee Orchestrator WebUI — app.js
 * Vanilla ES2020 module. No build step required.
 */

// ─── Config ───────────────────────────────────────────────────────────────────
const API_BASE = '/api/v1';

// ─── State ────────────────────────────────────────────────────────────────────
const STATE = {
  token: null,
  identity: null,
  channel: null,
  identityResolved: null,   // numeric ID after @username resolution
  currentSessionId: null,
  isTyping: false,
  pendingFiles: [],         // [{filename, file_path, mime_type}]
  sessions: [],
  activeSessionId: null,
};

// ─── Persist ──────────────────────────────────────────────────────────────────
function saveAuth() {
  localStorage.setItem('wee_token',    STATE.token    || '');
  localStorage.setItem('wee_identity', STATE.identity || '');
  localStorage.setItem('wee_channel',  STATE.channel  || '');
}

function loadAuth() {
  STATE.token    = localStorage.getItem('wee_token')    || null;
  STATE.identity = localStorage.getItem('wee_identity') || null;
  STATE.channel  = localStorage.getItem('wee_channel')  || null;
}

function clearAuth() {
  STATE.token = STATE.identity = STATE.channel = STATE.identityResolved = null;
  STATE.currentSessionId = STATE.activeSessionId = null;
  STATE.sessions = [];
  STATE.pendingFiles = [];
  localStorage.removeItem('wee_token');
  localStorage.removeItem('wee_identity');
  localStorage.removeItem('wee_channel');
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

  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
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

// Fetch a blob for authenticated image display
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
  // reset to step 1
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
  $('sidebar-identity').textContent = `${STATE.channel} · ${STATE.identity}`;
}

function showError(id, msg) {
  const el = $(id);
  el.textContent = msg;
  show(el);
}

function hideError(id) { hide($(id)); }

// ─── Auth Flow ────────────────────────────────────────────────────────────────
let _authState = 'IDLE'; // IDLE | CODE_SENT | LOGGED_IN

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

// ─── Session Management ────────────────────────────────────────────────────────
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

    const title = s.title || s.session_id;
    const preview = s.preview || '';

    item.innerHTML = `
      <div class="session-title">${escHtml(title)}</div>
      <div class="session-preview">${escHtml(preview)}</div>
      <button class="session-delete-btn" data-id="${escHtml(s.session_id)}" title="Delete">✕</button>
    `;

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
  STATE.activeSessionId = sessionId;
  STATE.currentSessionId = sessionId;

  // Update header
  $('header-session-id').textContent = sessionId;

  // Mark active in sidebar
  document.querySelectorAll('.session-item').forEach(el => {
    el.classList.toggle('active', el.dataset.sessionId === sessionId);
  });

  // Load messages
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
}

async function startNewSession() {
  try {
    const data = await apiRequest('POST', '/sessions/create', {});
    STATE.currentSessionId = data.session_id;
    STATE.activeSessionId  = data.session_id;
    $('header-session-id').textContent = data.session_id;
    clearMessages();
    hide($('empty-state'));
    // Add to session list immediately
    await loadSessions();
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

  // Ensure we have a session
  if (!STATE.currentSessionId) {
    await startNewSession();
  }

  // Append file paths to query (same pattern as Telegram/WebEx connectors)
  const fileRefs = STATE.pendingFiles.map(f => f.file_path);
  if (fileRefs.length) {
    query += '\n\nFiles attached:\n' + fileRefs.map(p => p).join('\n');
  }

  const fileNames = STATE.pendingFiles.map(f => f.filename);

  // Render user message
  await renderMessage('user', query, fileNames);

  // Clear input
  textarea.value = '';
  autoResizeTextarea(textarea);
  clearPendingFiles();
  $('btn-send').disabled = true;

  // Show typing indicator
  showTyping();

  try {
    const data = await apiRequest('POST', `/sessions/${STATE.currentSessionId}/execute`, { query });
    hideTyping();
    await renderMessage('assistant', data.response || '(no response)', []);
    // Refresh session list to update titles/previews
    await loadSessions();
  } catch (err) {
    hideTyping();
    renderSystemMessage('Error: ' + err.message);
  } finally {
    $('btn-send').disabled = false;
    scrollToBottom();
  }
}

// ─── Render Messages ──────────────────────────────────────────────────────────
function clearMessages() {
  const container = $('messages');
  container.innerHTML = '';
  // Keep empty-state div available but hidden
  const es = document.createElement('div');
  es.id = 'empty-state';
  es.className = 'empty-state hidden';
  es.innerHTML = '<div class="empty-icon">💬</div><p>Start a conversation or select a session from the sidebar.</p>';
  container.appendChild(es);
}

async function renderMessage(role, content, files = []) {
  hide($('empty-state'));

  const container = $('messages');
  const row = document.createElement('div');
  row.className = `message-row ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.textContent = role === 'user' ? '👤' : '🤖';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';

  if (role === 'user') {
    bubble.textContent = content;
  } else {
    // Render markdown
    try {
      bubble.innerHTML = marked.parse(content);
      // Syntax-highlight code blocks
      bubble.querySelectorAll('pre code').forEach(block => {
        if (window.hljs) hljs.highlightElement(block);
      });
    } catch (_) {
      bubble.textContent = content;
    }
  }

  // Load file images via fetch (handles auth headers)
  for (const fname of files) {
    if (/\.(png|jpe?g|gif|webp|svg)$/i.test(fname)) {
      const url = `${API_BASE}/uploads/${STATE.currentSessionId}/${encodeURIComponent(fname)}`;
      const blobUrl = await fetchBlob(url).catch(() => null);
      if (blobUrl) {
        const img = document.createElement('img');
        img.src = blobUrl;
        img.className = 'message-image';
        img.alt = fname;
        img.title = fname;
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
function showTyping() {
  STATE.isTyping = true;
  show($('typing-indicator'));
  scrollToBottom();
}

function hideTyping() {
  STATE.isTyping = false;
  hide($('typing-indicator'));
}

// ─── File Uploads ─────────────────────────────────────────────────────────────
async function handleFileSelect(file) {
  if (!STATE.currentSessionId) {
    await startNewSession();
  }

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
  if (STATE.pendingFiles.length === 0) {
    hide(strip);
    strip.innerHTML = '';
    return;
  }
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

function clearPendingFiles() {
  STATE.pendingFiles = [];
  renderFilePreviews();
}

// ─── Input Helpers ────────────────────────────────────────────────────────────
function autoResizeTextarea(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 180) + 'px';
}

function updateSendButton() {
  const val = $('message-input').value.trim();
  $('btn-send').disabled = !val && STATE.pendingFiles.length === 0;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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
  STATE.currentSessionId = null;
  STATE.activeSessionId  = null;
  await loadSessions();
  // If there are existing sessions, show empty state (user can pick one)
  if (STATE.sessions.length === 0) {
    await startNewSession();
  } else {
    show($('empty-state'));
  }
}

// ─── Boot ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // --- Auth UI ---
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

  // --- App UI ---
  $('btn-new-chat').addEventListener('click', startNewSession);
  $('btn-logout').addEventListener('click', () => { clearAuth(); showAuthView(); });

  $('btn-sidebar-toggle').addEventListener('click', () => toggleSidebar(false));
  $('btn-open-sidebar').addEventListener('click',  () => toggleSidebar(true));

  $('btn-send').addEventListener('click', sendMessage);

  const textarea = $('message-input');
  textarea.addEventListener('input', () => {
    autoResizeTextarea(textarea);
    updateSendButton();
  });
  textarea.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // File input
  $('file-input').addEventListener('change', e => {
    const file = e.target.files[0];
    if (file) handleFileSelect(file);
    e.target.value = ''; // reset so same file can be re-picked
  });

  // Drag-and-drop onto messages area
  $('messages').addEventListener('dragover', e => { e.preventDefault(); });
  $('messages').addEventListener('drop', e => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) handleFileSelect(file);
  });

  // --- Bootstrap ---
  loadAuth();
  if (STATE.token) {
    showAppView();
    initApp();
  } else {
    showAuthView();
  }
});
