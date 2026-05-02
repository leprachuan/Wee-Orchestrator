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
  isProcessing:    false,  // true when waiting for AI response
  isTyping:        false,
  pendingFiles:    [],
  sessions:        [],
  activeSessionId: null,
  schedulerEnabled: true,  // overridden by /api/v1/config on boot
  bgTasksEnabled:  true,  // overridden by /api/v1/config on boot
  requestQueue:    [],     // queued requests while processing
  queuePaused:     false,  // true to prevent auto-submit of next queued message
  pagination:      {},     // per-session pagination state: { sessionId: { offset, total } }
  currentProcessingQueueId: null,  // ID of queue item currently being processed
  currentAbortController: null,    // AbortController for the active streaming fetch
  fileViewerOpen:  false,          // whether file viewer panel is open
  fileViewerRaw:   false,          // true = raw view, false = preview
  fileViewerPath:  null,           // currently viewed file path
  fileViewerData:  null,           // cached file data
  // Per-session stream tracking for multi-session streaming support
  sessionStreams:  {},             // sessionId -> { isProcessing, abortController, query }
  silentMode:     false,          // F027: tool call visibility (true=hidden)
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

// ─── Text-to-Speech ──────────────────────────────────────────────────────────
const TTS = {
  currentAudio: null,
  currentBtn: null,

  /** Extract plain text from a message bubble's innerHTML. */
  extractText(bubble) {
    // Clone to avoid modifying the DOM
    const clone = bubble.cloneNode(true);
    // Remove code blocks
    clone.querySelectorAll('pre, code').forEach(el => el.remove());
    // Remove TTS button itself
    clone.querySelectorAll('.tts-btn').forEach(el => el.remove());
    return (clone.textContent || '').trim();
  },

  /** Stop any currently playing audio. */
  stop() {
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio.src = '';
      this.currentAudio = null;
    }
    if (this.currentBtn) {
      this.currentBtn.classList.remove('tts-loading', 'tts-playing');
      this.currentBtn.innerHTML = '🔊';
      this.currentBtn.title = 'Read aloud';
      this.currentBtn = null;
    }
  },

  /** Generate and play TTS for a bubble. */
  async play(btn, bubble) {
    const text = this.extractText(bubble);
    if (!text) return;

    // If same button is already playing, stop it
    if (this.currentBtn === btn && this.currentAudio && !this.currentAudio.paused) {
      this.stop();
      return;
    }

    // Stop any other playing audio first
    this.stop();

    this.currentBtn = btn;
    btn.classList.add('tts-loading');
    btn.innerHTML = '⏳';
    btn.title = 'Generating speech…';

    try {
      const res = await fetch(`${API_BASE}/tts`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${STATE.token}`,
        },
        body: JSON.stringify({ text }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);

      const audio = new Audio(url);
      this.currentAudio = audio;

      audio.addEventListener('ended', () => {
        URL.revokeObjectURL(url);
        btn.classList.remove('tts-playing');
        btn.innerHTML = '🔊';
        btn.title = 'Read aloud';
        this.currentAudio = null;
        this.currentBtn = null;
      });

      audio.addEventListener('error', () => {
        URL.revokeObjectURL(url);
        btn.classList.remove('tts-loading', 'tts-playing');
        btn.innerHTML = '🔊';
        btn.title = 'Read aloud';
        this.currentAudio = null;
        this.currentBtn = null;
      });

      btn.classList.remove('tts-loading');
      btn.classList.add('tts-playing');
      btn.innerHTML = '⏹';
      btn.title = 'Stop playback';

      await audio.play();
    } catch (err) {
      console.error('[TTS] Error:', err);
      btn.classList.remove('tts-loading', 'tts-playing');
      btn.innerHTML = '🔊';
      btn.title = 'Read aloud';
      this.currentAudio = null;
      this.currentBtn = null;
    }
  },
};

/** Create a TTS play button element for an assistant bubble. */
function createTtsButton(bubble) {
  const btn = document.createElement('button');
  btn.className = 'tts-btn';
  btn.innerHTML = '🔊';
  btn.title = 'Read aloud';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    TTS.play(btn, bubble);
  });
  return btn;
}

const $ = id => document.getElementById(id);
const show = el => el.classList.remove('hidden');
const hide = el => el.classList.add('hidden');

function isMobileViewport() {
  return window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
}

function updateMobileViewportVars() {
  if (!isMobileViewport()) return;
  const h = (window.visualViewport && window.visualViewport.height) ? window.visualViewport.height : window.innerHeight;
  document.documentElement.style.setProperty('--vh', `${h * 0.01}px`);
}

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
  // Apply background tasks feature flag
  if (!STATE.bgTasksEnabled) {
    hide($('btn-nav-background'));
    hide($('background-panel'));
  } else {
    // Start background task polling
    startBgTaskPolling();
  }
  // Start notification polling (always, if notifications are supported)
  startNotificationPolling();
  // Start in-thread bg-events polling (F017)
  startBgEventPolling();
}

function updateSidebarIdentity() {
  const label = STATE.username
    ? `@${STATE.username}`
    : (STATE.identity || '—');
  $('sidebar-identity').textContent = `${STATE.channel || ''} · ${label}`;
}

function showError(id, msg) { const el = $(id); el.textContent = msg; show(el); }
function hideError(id) { hide($(id)); }

// ─── Runtime Brand Icons ──────────────────────────────────────────────────────
const RUNTIME_ICONS = {
  claude:      '/ui/assets/runtime-icons/claude.svg',
  'claude-sdk': '/ui/assets/runtime-icons/claude.svg',
  copilot:     '/ui/assets/runtime-icons/copilot.svg',
  'copilot-sdk': '/ui/assets/runtime-icons/copilot.svg',
  gemini:      '/ui/assets/runtime-icons/gemini.svg',
  opencode:    '/ui/assets/runtime-icons/opencode.svg',
  codex:       '/ui/assets/runtime-icons/openai.svg',
  devin:       '/ui/assets/runtime-icons/devin.svg',
  cursor:      '/ui/assets/runtime-icons/cursor.svg',
  wee:         '/ui/assets/runtime-icons/wee.svg',
};

function runtimeIconHTML(runtime, size = 14) {
  const src = RUNTIME_ICONS[String(runtime || '').toLowerCase()];
  if (!src) return '';
  return `<img src="${src}" class="runtime-icon" width="${size}" height="${size}" alt="" aria-hidden="true">`;
}

// ─── Session Meta Pills ───────────────────────────────────────────────────────
// F027: Verbose mode toggle — update UI state
function _updateVerboseToggleUI(silent) {
  const btn = document.getElementById('btn-verbose-toggle');
  if (!btn) return;
  const msgContainer = document.getElementById('messages');
  if (silent) {
    btn.textContent = '🔇';
    btn.title = 'Tool calls hidden — click to show';
    btn.setAttribute('aria-pressed', 'true');
    btn.classList.add('verbose-off');
    if (msgContainer) msgContainer.classList.add('tool-calls-hidden');
  } else {
    btn.textContent = '⚙️';
    btn.title = 'Tool calls visible — click to hide';
    btn.setAttribute('aria-pressed', 'false');
    btn.classList.remove('verbose-off');
    if (msgContainer) msgContainer.classList.remove('tool-calls-hidden');
  }
}

function updateSessionMeta(data) {
  const set = (id, text, extra = '') => {
    const el = $(id);
    if (!text || text === 'null' || text === 'undefined') {
      el.textContent = '—';
      el.classList.add('empty');
      if (extra) el.classList.remove(extra);
    } else {
      if (id === 'meta-runtime') {
        el.dataset.runtime = text;
        el.innerHTML = runtimeIconHTML(text) + escHtml(text);
        el.removeAttribute('title');
      } else {
        el.textContent = text;
      }
      el.classList.remove('empty');
      if (extra) el.classList.toggle(extra, true);
    }
  };

  set('meta-agent',   data?.agent);
  set('meta-runtime', data?.runtime);

  // Shorten model names for display
  const model = data?.model ? data.model.replace(/^claude-/, '').replace(/^gpt-/, '') : null;
  set('meta-model', model);

  // Mode pill mirrors the permissions mode

  // Permissions pill
  const permEl = $('meta-permissions');
  if (permEl) {
    const perms = data?.permissions;
    const permMode = perms?.mode || 'restricted';
    const permIcons = { elevated: '⚡', restricted: '🔒', sandboxed: '🏖️' };
    const permLabels = { elevated: 'Full Access', restricted: 'Restricted', sandboxed: 'Sandboxed' };
    permEl.textContent = (permIcons[permMode] || '🔒') + ' ' + (permLabels[permMode] || permMode);
    permEl.dataset.permMode = permMode;
    permEl.classList.remove('empty');
    permEl.classList.toggle('perm-elevated', permMode === 'elevated');
    permEl.classList.toggle('perm-sandboxed', permMode === 'sandboxed');
  }


  // F027: Verbose toggle — sync from session status
  const _verboseBtn = document.getElementById('btn-verbose-toggle');
  if (_verboseBtn) {
    const _silentMode = !!data?.silent_mode;
    STATE.silentMode = _silentMode;
    _updateVerboseToggleUI(_silentMode);
  }

}

async function fetchAndUpdateMeta(sessionId) {
  if (!sessionId) return;
  try {
    const data = await apiRequest('GET', `/sessions/${sessionId}/status`);
    updateSessionMeta(data);

    // If the server has a stream buffer for this session (running or recently
    // finished) and we aren't already processing or reconnecting, reconnect
    // to replay any missed output and resume live streaming.
    if (data.has_stream_buffer
        && sessionId === STATE.currentSessionId
        && !STATE.isProcessing
        && !(STATE.sessionStreams[sessionId] && STATE.sessionStreams[sessionId].reconnectAttempted)) {
      STATE.sessionStreams[sessionId] = {
        ...(STATE.sessionStreams[sessionId] || {}),
        reconnectAttempted: true,
      };
      reconnectToStream(sessionId);
    }
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
    options: null,
    dynamicLoad: async () => {
      try {
        const data = await apiRequest('GET', '/agents');
        const opts = (data.agents || []).map(a => ({
          label: a.name,
          cmd: `/agent set ${a.name}`,
        }));
        opts.push({ label: '📋 list agents', cmd: '/agent list' });
        return opts;
      } catch (e) {
        return [{ label: '📋 list agents', cmd: '/agent list' }];
      }
    },
  },
  'meta-runtime': {
    label: 'Switch Runtime',
    options: null,   // null = dynamically loaded
    dynamicLoad: async () => {
      try {
        const data = await apiRequest('GET', '/runtimes');
        const opts = (data.runtimes || []).map(r => ({
          label: `${runtimeIconHTML(r.id)}${r.label}`,
          cmd: `/runtime set ${r.id}`,
        }));
        return opts;
      } catch (e) {
        // Fallback to basic runtimes if API fails
        return [
          { label: `${runtimeIconHTML('claude')}claude`,         cmd: '/runtime set claude' },
          { label: `${runtimeIconHTML('copilot')}copilot`,       cmd: '/runtime set copilot' },
          { label: `${runtimeIconHTML('gemini')}gemini`,         cmd: '/runtime set gemini' },
          { label: `${runtimeIconHTML('opencode')}opencode`,     cmd: '/runtime set opencode' },
          { label: `${runtimeIconHTML('codex')}codex`,           cmd: '/runtime set codex' },
          { label: `${runtimeIconHTML('devin')}devin`,           cmd: '/runtime set devin' },
          { label: `${runtimeIconHTML('cursor')}cursor`,         cmd: '/runtime set cursor' },
          { label: `${runtimeIconHTML('wee')}wee`,             cmd: '/runtime set wee' },
        ];
      }
    },
  },
  'meta-model': {
    label: 'Switch Model',
    options: null,   // null = dynamically loaded
    dynamicLoad: async () => {
      const runtime = $('meta-runtime')?.dataset?.runtime || $('meta-runtime')?.textContent?.trim() || 'copilot';
      try {
        const data = await apiRequest('GET', `/models?runtime=${encodeURIComponent(runtime)}`);
        const models = data.models || [];
        const opts = [];
        let lastGroup = null;
        models.forEach(m => {
          if (m.group && m.group !== lastGroup) {
            opts.push({ label: '── ' + m.group + ' ──', cmd: null, disabled: true });
            lastGroup = m.group;
          }
          opts.push({ label: m.label, cmd: `/model set ${m.id}` });
        });
        opts.push({ label: '📋 list models', cmd: '/model list' });
        return opts;
      } catch (e) {
        return [{ label: '📋 list models', cmd: '/model list' }];
      }
    },
  },
  'meta-permissions': {
    label: 'Session Permissions',
    options: null,
    dynamicLoad: async () => {
      try {
        const data = await apiRequest('GET', '/permissions/templates');
        return (data.templates || []).map(t => ({
          label: t.icon + ' ' + t.label,
          value: t.mode,
          cmd: null,
          action: async () => {
            if (!STATE.currentSessionId) return;
            try {
              await apiRequest('PUT', '/sessions/' + STATE.currentSessionId + '/permissions', { mode: t.mode });
              fetchAndUpdateMeta(STATE.currentSessionId);
            } catch (e) { console.error('Failed to set permissions:', e); }
          },
        }));
      } catch (e) {
        return [
          { label: '⚡ Full Access', value: 'elevated', action: async () => { if (STATE.currentSessionId) { await apiRequest('PUT', '/sessions/' + STATE.currentSessionId + '/permissions', { mode: 'elevated' }); fetchAndUpdateMeta(STATE.currentSessionId); } } },
          { label: '🔒 Restricted', value: 'restricted', action: async () => { if (STATE.currentSessionId) { await apiRequest('PUT', '/sessions/' + STATE.currentSessionId + '/permissions', { mode: 'restricted' }); fetchAndUpdateMeta(STATE.currentSessionId); } } },
          { label: '🏖️ Sandboxed', value: 'sandboxed', action: async () => { if (STATE.currentSessionId) { await apiRequest('PUT', '/sessions/' + STATE.currentSessionId + '/permissions', { mode: 'sandboxed' }); fetchAndUpdateMeta(STATE.currentSessionId); } } },
        ];
      }
    },
  },
};

let _pillPopover = null;

function hidePillPopover() {
  if (_pillPopover) { _pillPopover.remove(); _pillPopover = null; }
}

function buildPopoverDOM(pillEl, label, options) {
  const popover = document.createElement('div');
  popover.className = 'pill-popover glass-panel';
  const header = document.createElement('div');
  header.className = 'pill-popover-header';
  header.textContent = label;
  popover.appendChild(header);
  options.forEach(opt => {
    const item = document.createElement('button');
    item.className = 'pill-popover-item';
    item.innerHTML = opt.label;
    if (!opt.cmd && !opt.action) { item.disabled = true; item.style.opacity = '0.5'; }
    item.addEventListener('mousedown', e => {
      e.preventDefault();
      if (!opt.cmd && !opt.action) return;
      hidePillPopover();
      if (opt.action) { opt.action(); } else { sendCommand(opt.cmd); }
    });
    item.addEventListener('click', e => {
      if (!isMobileViewport()) return;
      e.preventDefault();
      if (!opt.cmd && !opt.action) return;
      hidePillPopover();
      if (opt.action) { opt.action(); } else { sendCommand(opt.cmd); }
    });
    popover.appendChild(item);
  });
  document.body.appendChild(popover);
  _pillPopover = popover;
  const rect = pillEl.getBoundingClientRect();
  const maxPopH = Math.min(window.innerHeight * 0.6, 480);
  const popH = Math.min(popover.offsetHeight || 200, maxPopH);
  let top = rect.bottom + 6;
  if (top + popH > window.innerHeight - 10) top = rect.top - popH - 6;
  if (top < 8) top = 8;
  let left = rect.right - popover.offsetWidth;
  if (left < 8) left = 8;
  popover.style.top  = `${top}px`;
  popover.style.left = `${left}px`;
}

async function showPillPopover(pillEl, pillId) {
  hidePillPopover();
  const config = PILL_OPTIONS[pillId];
  if (!config) return;

  if (config.options) {
    buildPopoverDOM(pillEl, config.label, config.options);
  } else if (config.dynamicLoad) {
    buildPopoverDOM(pillEl, config.label, [{ label: '⏳ Loading…', cmd: null }]);
    const capturedRef = _pillPopover;
    const opts = await config.dynamicLoad();
    if (_pillPopover === capturedRef) {  // still open and same popover
      hidePillPopover();
      buildPopoverDOM(pillEl, config.label, opts);
    }
  }
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
  { cmd: '/mode',         usage: '/mode <elevated|restricted|sandboxed|current|list>',   desc: 'Switch permission mode (elevated / restricted / sandboxed)' },
  { cmd: '/status',       usage: '/status',                                desc: 'Show current session status' },
  { cmd: '/cancel',       usage: '/cancel',                                desc: 'Cancel a running query' },
  { cmd: '/capabilities', usage: '/capabilities',                          desc: 'List all available capabilities' },
  { cmd: '/session',      usage: '/session',                               desc: 'Show session info' },
  { cmd: '/timeout',      usage: '/timeout <seconds>',                     desc: 'Set the command timeout' },
  { cmd: '/render',       usage: '/render <text|markdown>',                desc: 'Set render output type' },
  { cmd: '/update',       usage: '/update',                                desc: 'Pull latest code and restart dev services (aliases: /upgrade, /pull)' },
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
    { sub: 'elevated',    desc: 'Full access — auto-approve all actions (no prompts)' },
    { sub: 'restricted',  desc: 'Require approval for potentially destructive actions' },
    { sub: 'sandboxed',   desc: 'Read-only sandbox — no writes, no network, no installs' },
    { sub: 'current',     desc: 'Show the current permission mode' },
    { sub: 'list',        desc: 'List available permission modes' },
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
    row.addEventListener('click', e => {
      if (!isMobileViewport()) return;
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
    const agentName = s.agent || '';
    const agentBadge = agentName
      ? `<span class="session-agent-badge" data-agent="${escHtml(agentName)}" title="${escHtml(agentName)}">${escHtml(agentName)}</span>`
      : '';

    item.innerHTML =
      `<div class="session-title" title="Double-click to rename">${agentBadge}${escHtml(title)}</div>` +
      `<div class="session-preview">${escHtml(preview)}</div>` +
      `<button class="session-rename-btn" data-id="${escHtml(s.session_id)}" title="Rename">✏️</button>` +
      `<button class="session-delete-btn" data-id="${escHtml(s.session_id)}" title="Delete">✕</button>`;

    item.addEventListener('click', e => {
      if (e.target.classList.contains('session-delete-btn') ||
          e.target.classList.contains('session-rename-btn') ||
          e.target.classList.contains('session-rename-input')) return;
      selectSession(s.session_id);
      if (isMobileViewport()) toggleSidebar(false);
    });
    // Double-click on title to rename
    item.querySelector('.session-title').addEventListener('dblclick', e => {
      e.stopPropagation();
      startInlineRename(item, s.session_id, s.title || s.session_id);
    });
    item.querySelector('.session-rename-btn').addEventListener('click', e => {
      e.stopPropagation();
      startInlineRename(item, s.session_id, s.title || s.session_id);
    });
    item.querySelector('.session-delete-btn').addEventListener('click', e => {
      e.stopPropagation();
      deleteSession(s.session_id);
    });
    list.appendChild(item);
  }
}

/** Start inline rename editing for a session item */
function startInlineRename(item, sessionId, currentTitle) {
  const titleEl = item.querySelector('.session-title');
  if (titleEl.querySelector('.session-rename-input')) return; // already editing

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'session-rename-input';
  input.value = currentTitle;
  input.maxLength = 120;

  // Preserve the agent badge element before clearing title
  const existingBadge = titleEl.querySelector('.session-agent-badge');
  const badgeHtml = existingBadge ? existingBadge.outerHTML : '';
  titleEl.textContent = '';
  titleEl.appendChild(input);
  input.focus();
  input.select();

  const _rebuildTitle = (text) => {
    titleEl.innerHTML = badgeHtml + escHtml(text);
  };

  const commitRename = async () => {
    const newTitle = input.value.trim();
    if (!newTitle || newTitle === currentTitle) {
      _rebuildTitle(currentTitle);
      return;
    }
    try {
      await apiRequest('PATCH', `/history/sessions/${sessionId}`, { title: newTitle });
      // Update local state
      const sess = STATE.sessions.find(s => s.session_id === sessionId);
      if (sess) sess.title = newTitle;
      _rebuildTitle(newTitle);
    } catch (err) {
      _rebuildTitle(currentTitle);
      console.error('Rename failed:', err);
    }
  };

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = currentTitle; input.blur(); }
  });
  input.addEventListener('blur', commitRename, { once: true });
}

async function selectSession(sessionId) {
  const previousSessionId = STATE.activeSessionId;

  // If switching away from a session with an active stream, abort the fetch
  // but mark it as still processing so we can reconnect later.
  if (previousSessionId && previousSessionId !== sessionId) {
    if (STATE.currentAbortController) {
      STATE.currentAbortController.abort();
      STATE.currentAbortController = null;
    }
    // Save processing state for the departing session
    if (STATE.isProcessing) {
      STATE.sessionStreams[previousSessionId] = {
        ...(STATE.sessionStreams[previousSessionId] || {}),
        isProcessing: true,
      };
    }
  }

  STATE.activeSessionId  = sessionId;
  STATE.currentSessionId = sessionId;
  $('header-session-id').textContent = sessionId;

  document.querySelectorAll('.session-item').forEach(el =>
    el.classList.toggle('active', el.dataset.sessionId === sessionId)
  );

  clearMessages();

  // Reset global processing state — will be set true if we reconnect
  STATE.isProcessing = false;
  hideTyping();

  try {
    const data = await apiRequest('GET', `/history/sessions/${sessionId}/messages?limit=100`);
    const msgs = data.messages || [];
    const total = data.total || msgs.length;
    const offset = data.offset || 0;
    STATE.pagination[sessionId] = { offset, total };
    for (const msg of msgs) {
      await renderMessage(msg.role, msg.content, msg.files || []);
    }
    updateLoadMoreButton();
  } catch (err) {
    renderSystemMessage('Could not load messages: ' + err.message);
  }
  scrollToBottom();
  await fetchAndUpdateMeta(sessionId);
  loadScratchNotes();   // load scratch notes for this session
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
    loadScratchNotes();   // load scratch notes for new session (will be empty)
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

// ─── REQUEST QUEUE MANAGEMENT ──────────────────────────────────────────────

function generateQueueId() {
  return Math.random().toString(36).substring(2) + Date.now().toString(36);
}

function queueRequest(text, files = []) {
  const queueItem = {
    id: generateQueueId(),
    text: text,
    files: files,
    timestamp: Date.now(),
    status: 'pending'
  };
  STATE.requestQueue.push(queueItem);
  renderQueuePanel();
  showQueuePanel();
}

function setQueueItemStatus(queueId, status) {
  const item = STATE.requestQueue.find(q => q.id === queueId);
  if (item) {
    item.status = status;
    renderQueuePanel();
  }
}

function markCurrentQueueAsProcessing(queueId) {
  STATE.currentProcessingQueueId = queueId;
  setQueueItemStatus(queueId, 'processing');
}

function markCurrentQueueAsCompleted() {
  if (STATE.currentProcessingQueueId) {
    setQueueItemStatus(STATE.currentProcessingQueueId, 'completed');
    STATE.currentProcessingQueueId = null;
  }
}

function markCurrentQueueAsFailed() {
  if (STATE.currentProcessingQueueId) {
    setQueueItemStatus(STATE.currentProcessingQueueId, 'failed');
    STATE.currentProcessingQueueId = null;
  }
}

function processNextQueue() {
  if (STATE.requestQueue.length === 0) {
    STATE.isProcessing = false;
    return;
  }

  // Get first item and remove from queue
  const nextRequest = STATE.requestQueue.shift();
  renderQueuePanel();

  // Prepare textarea with queued request
  const textarea = $('message-input');
  textarea.value = nextRequest.text;
  autoResizeTextarea(textarea);
  syncMirror();

  // Restore pending files
  STATE.pendingFiles = nextRequest.files || [];
  renderFilePreviews();

  // Mark this item as processing
  markCurrentQueueAsProcessing(nextRequest.id);

  // Send the request
  sendMessage();
}

function deleteQueueItem(queueId) {
  STATE.requestQueue = STATE.requestQueue.filter(item => item.id !== queueId);
  renderQueuePanel();
}

function editQueueItem(queueId) {
  const item = STATE.requestQueue.find(q => q.id === queueId);
  if (!item) return;

  // Remove from queue
  STATE.requestQueue = STATE.requestQueue.filter(q => q.id !== queueId);

  // Put back in textarea for editing
  const textarea = $('message-input');
  textarea.value = item.text;
  autoResizeTextarea(textarea);
  syncMirror();
  renderQueuePanel();
}

function showQueuePanel() {
  const panel = $('request-queue-panel');
  if (panel) {
    panel.classList.remove('queue-hidden');
  }
}

function hideQueuePanel() {
  const panel = $('request-queue-panel');
  if (panel) {
    panel.classList.add('queue-hidden');
  }
}

function renderQueuePanel() {
  const queueList = $('queue-items-list');
  if (!queueList) return;

  const pauseBtn = $('btn-pause-queue');
  const statusMsg = $('queue-status-msg');
  const queueSection = $('queue-section');
  
  if (pauseBtn) {
    pauseBtn.textContent = STATE.queuePaused ? '▶' : '⏸';
    pauseBtn.title = STATE.queuePaused ? 'Resume auto-submit' : 'Pause auto-submit';
  }
  if (statusMsg) {
    statusMsg.textContent = STATE.queuePaused
      ? 'Queue paused — click ▶ to resume auto-submit'
      : 'Requests will auto-submit when current message completes';
  }

  if (STATE.requestQueue.length === 0) {
    queueList.innerHTML = '<p class="queue-empty"><img src="/static/icon-192.png" alt="" style="width:48px;height:48px;border-radius:10px;opacity:0.5;display:block;margin:0 auto 8px;">No queued requests</p>';
    const counter = $('queue-count');
    if (counter) counter.textContent = '0';
    // Collapse queue section when empty
    if (queueSection) {
      queueSection.classList.add('queue-section-collapsed');
    }
    return;
  }

  const counter = $('queue-count');
  if (counter) counter.textContent = STATE.requestQueue.length;
  
  // Expand queue section when items added
  if (queueSection) {
    queueSection.classList.remove('queue-section-collapsed');
  }

  queueList.innerHTML = STATE.requestQueue.map((item, idx) => {
    const preview = item.text.substring(0, 60).replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const fileCount = item.files ? item.files.length : 0;
    const fileLabel = fileCount > 0 ? ` 📎 ${fileCount}` : '';
    const time = new Date(item.timestamp).toLocaleTimeString();

    let statusClass = 'queue-status-pending';
    let statusSymbol = '◯';
    if (item.status === 'completed') { statusClass = 'queue-status-completed'; statusSymbol = '●'; }
    else if (item.status === 'failed') { statusClass = 'queue-status-failed'; statusSymbol = '●'; }
    else if (item.status === 'processing') { statusClass = 'queue-status-processing'; statusSymbol = '◐'; }

    return `
      <div class="queue-item ${statusClass}" data-queue-id="${item.id}">
        <div class="queue-item-header">
          <span class="queue-status-dot ${statusClass}">${statusSymbol}</span>
          <span class="queue-item-number">#${idx + 1}</span>
          <span class="queue-item-time">${time}</span>
        </div>
        <div class="queue-item-preview">"${preview}${preview.length >= 60 ? '...' : ''}"${fileLabel}</div>
        <div class="queue-item-actions">
          <button class="queue-btn-edit" title="Edit request">✎ Edit</button>
          <button class="queue-btn-delete" title="Delete request">✕ Delete</button>
        </div>
      </div>
    `;
  }).join('');

  queueList.querySelectorAll('.queue-btn-edit').forEach(btn => {
    btn.addEventListener('click', () => {
      const queueId = btn.closest('.queue-item').dataset.queueId;
      editQueueItem(queueId);
    });
  });

  queueList.querySelectorAll('.queue-btn-delete').forEach(btn => {
    btn.addEventListener('click', () => {
      const queueId = btn.closest('.queue-item').dataset.queueId;
      deleteQueueItem(queueId);
    });
  });
}

function toggleQueuePanel() {
  const panel = $('request-queue-panel');
  if (panel) {
    panel.classList.toggle('queue-minimized');
    const btn = $('btn-toggle-queue');
    if (btn) {
      btn.textContent = panel.classList.contains('queue-minimized') ? '›' : '‹';
      btn.title = panel.classList.contains('queue-minimized') ? 'Expand queue' : 'Collapse queue';
    }
  }
}

function toggleQueuePause() {
  STATE.queuePaused = !STATE.queuePaused;
  renderQueuePanel();
  if (!STATE.queuePaused && !STATE.isProcessing && STATE.requestQueue.length > 0) {
    processNextQueue();
  }
}

function toggleQueueSection() {
  const section = $('queue-section');
  if (section) {
    section.classList.toggle('queue-section-collapsed');
  }
}

async function saveScratchNotes(text) {
  if (!STATE.currentSessionId) return;
  
  try {
    await apiRequest('POST', `/sessions/${STATE.currentSessionId}/scratch`, { scratch: text });
  } catch (err) {
    console.warn('Error saving scratch notes:', err);
  }
}

async function loadScratchNotes() {
  if (!STATE.currentSessionId) return;
  
  try {
    const data = await apiRequest('GET', `/sessions/${STATE.currentSessionId}/scratch`);
    const scratchTextarea = $('scratch-textarea');
    if (scratchTextarea) {
      scratchTextarea.value = data.scratch || "";
      const charCountEl = $('scratch-char-count');
      if (charCountEl) charCountEl.textContent = (data.scratch || "").length;
    }
  } catch (err) {
    console.warn('Error loading scratch notes:', err);
  }
}

// ─── Messaging ────────────────────────────────────────────────────────────────
async function sendMessage() {
  // If already processing, check for /cancel first — it bypasses the queue
  if (STATE.isProcessing) {
    const textarea = $('message-input');
    let query = textarea.value.trim();
    if (!query && STATE.pendingFiles.length === 0) return;

    if (query === '/cancel') {
      textarea.value = '';
      autoResizeTextarea(textarea);
      syncMirror();

      // Show /cancel as a user message
      await renderMessage('user', '/cancel', []);

      // Abort the in-flight streaming fetch
      if (STATE.currentAbortController) {
        STATE.currentAbortController.abort();
        STATE.currentAbortController = null;
      }

      // Call the dedicated cancel endpoint
      try {
        const headers = { 'Content-Type': 'application/json' };
        if (STATE.token) headers['Authorization'] = `Bearer ${STATE.token}`;
        const res = await fetch(`${API_BASE}/sessions/${STATE.currentSessionId}/cancel`, {
          method: 'POST',
          headers,
        });
        const data = await res.json();
        renderSystemMessage(data.cancelled ? `✓ ${data.message}` : `ℹ️ ${data.message}`);
      } catch (err) {
        renderSystemMessage('❌ Failed to cancel: ' + err.message);
      }

      // Clean up processing state
      hideTyping();
      STATE.isProcessing = false;
      delete STATE.sessionStreams[STATE.currentSessionId];
      $('btn-send').disabled = false;
      scrollToBottom();
      return;
    }

    queueRequest(query, [...STATE.pendingFiles]);

    // Clear input after queuing
    textarea.value = '';
    autoResizeTextarea(textarea);
    syncMirror();
    clearPendingFiles();
    $('btn-send').disabled = true;
    return;
  }

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

  STATE.isProcessing = true;
  const sendStartTime = performance.now();
  STATE.sessionStreams[STATE.currentSessionId] = { isProcessing: true, query, startTime: sendStartTime };
  showTyping();
  try {
    const result = await sendMessageStreaming(query, STATE.currentSessionId);
    hideTyping();
    STATE.isProcessing = false;
    delete STATE.sessionStreams[STATE.currentSessionId];

    // Mark current queue item as completed
    markCurrentQueueAsCompleted();

    // Auto-submit next queued request if any (unless queue is paused)
    if (STATE.requestQueue.length > 0 && !STATE.queuePaused) {
      processNextQueue();
    }

    // Refresh meta — a /agent set etc. may have changed things
    await fetchAndUpdateMeta(STATE.currentSessionId);
    await loadSessions();
  } catch (err) {
    hideTyping();
    STATE.isProcessing = false;
    delete STATE.sessionStreams[STATE.currentSessionId];

    // If aborted by /cancel, don't show an error — the cancel handler already reported
    if (err.name === 'AbortError') {
      // Clean up the streaming bubble if one was created
      const streamingBubble = document.querySelector('.streaming');
      if (streamingBubble) {
        streamingBubble.classList.remove('streaming');
        streamingBubble.textContent = '(cancelled)';
      }
    } else {
      // Mark current queue item as failed
      markCurrentQueueAsFailed();

      renderSystemMessage('Error: ' + err.message);
    }
  } finally {
    $('btn-send').disabled = false;
    scrollToBottom();
  }
}


/**
 * Extract a human-readable summary from a tool call input payload.
 */
function getToolInputSummary(toolName, input) {
  if (!input) return '';
  try {
    const inp = typeof input === 'string' ? JSON.parse(input) : input;
    if (/^bash$/i.test(toolName)) return (inp.command || inp.cmd || JSON.stringify(inp)).substring(0, 100);
    if (/read|write|edit|glob|view|create/i.test(toolName)) return (inp.file_path || inp.path || inp.pattern || JSON.stringify(inp)).substring(0, 100);
    if (/fetch|web/i.test(toolName)) return (inp.url || JSON.stringify(inp)).substring(0, 100);
    if (/grep/i.test(toolName)) return (inp.pattern || JSON.stringify(inp)).substring(0, 100);
    return JSON.stringify(inp).substring(0, 100);
  } catch {
    return String(input).substring(0, 100);
  }
}

/**
 * Insert an interleaved tool-call block row into the messages container.
 */
function insertToolCallBlock(streamBubble, toolId, toolName, inputSummary) {
  const wrapper = document.createElement('div');
  wrapper.className = 'tc-block';
  wrapper.id = 'tc-block-' + toolId;
  const line = document.createElement('div');
  line.className = 'tc-line';
  line.id = 'tc-' + toolId;
  line.innerHTML =
    '<span class=tc-toggle title=Expand output>▶</span>' +
    '<span class=tc-spinner spinning>⚙️</span>' +
    '<span class=tc-name>' + escHtml(toolName) + '</span>' +
    (inputSummary ? '<code class=tc-input>' + escHtml(inputSummary) + '</code>' : '') +
    '<span class=tc-status running>running…</span>';
  wrapper.appendChild(line);
  // Output container (hidden by default)
  const outputEl = document.createElement('div');
  outputEl.className = 'tc-output';
  outputEl.id = 'tc-output-' + toolId;
  wrapper.appendChild(outputEl);
  // Click handler for expand/collapse
  line.addEventListener('click', () => {
    wrapper.classList.toggle('tc-expanded');
    const toggle = line.querySelector('.tc-toggle');
    if (toggle) toggle.textContent = wrapper.classList.contains('tc-expanded') ? '▼' : '▶';
  });
  streamBubble.appendChild(wrapper);
  wrapper.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/**
 * Mark a tool-call block as complete (stop spinner, show checkmark).
 */
function completeToolCallBlock(toolId, output, isError) {
  const row = document.getElementById('tc-' + toolId);
  if (!row) return;
  const spinner = row.querySelector('.tc-spinner');
  if (spinner) spinner.classList.remove('spinning');
  const status = row.querySelector('.tc-status');
  if (isError) {
    if (status) { status.textContent = '✗ failed'; status.className = 'tc-status error'; }
    const wrapper = row.closest('.tc-block');
    if (wrapper) wrapper.classList.add('tc-error');
  } else {
    if (status) { status.textContent = '✓'; status.className = 'tc-status done'; }
  }
  // Store output if provided
  if (output) {
    const outputEl = document.getElementById('tc-output-' + toolId);
    if (outputEl) {
      outputEl.textContent = output;
      // Show toggle indicator that output is available
      const toggle = row.querySelector('.tc-toggle');
      if (toggle) toggle.classList.add('has-output');
    }
  }
}

/**
 * Stop all remaining active tool call spinners (cleanup on done).
 */
function cleanupAllToolSpinners() {
  document.querySelectorAll('.tc-spinner.spinning').forEach(el => {
    el.classList.remove('spinning');
    const status = el.closest('.tc-block')?.querySelector('.tc-status') ||
                   el.closest('.tc-line')?.querySelector('.tc-status');
    if (status && status.classList.contains('running')) {
      status.textContent = '✓'; status.className = 'tc-status done';
    }
  });
}

/* Legacy stub — kept so any stale references don't throw */
function refreshInlineToolIcons() {}

/**
 * Detect whether a raw output line looks like a tool call invocation.
 * Used to add gear markers when rendering background task logs.
 */
const INTERNAL_LINE_MARKERS = [
  '___BEGIN___COMMAND_DONE_MARKER___',
  '___END___COMMAND_DONE_MARKER___',
  '__COPILOT_DONE__',
];
function isInternalMarkerLine(line) {
  return INTERNAL_LINE_MARKERS.some(m => line.includes(m));
}

function looksLikeCodexTransportFrames(text) {
  if (!text || !text.trim()) return false;
  const trimmed = text.trim();
  return (
    trimmed.includes('"type":"thread.started"') ||
    trimmed.includes('"type":"turn.started"') ||
    trimmed.includes('"type":"turn.completed"') ||
    trimmed.includes('"type":"item.completed"') ||
    trimmed.includes('"type": "thread.started"') ||
    trimmed.includes('"type": "turn.started"') ||
    trimmed.includes('"type": "turn.completed"') ||
    trimmed.includes('"type": "item.completed"')
  );
}

function normalizeCodexStreamText(text) {
  const raw = String(text || '');
  if (!raw.trim()) return '';
  if (!looksLikeCodexTransportFrames(raw)) return raw;

  const out = [];
  for (const line of raw.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (!trimmed.startsWith('{')) {
      out.push(line);
      continue;
    }
    try {
      const evt = JSON.parse(trimmed);
      if (
        evt.type === 'item.completed' &&
        evt.item &&
        evt.item.type === 'agent_message' &&
        typeof evt.item.text === 'string'
      ) {
        if (evt.item.text) out.push(evt.item.text);
        continue;
      }
      if (
        evt.type === 'thread.started' ||
        evt.type === 'thread.completed' ||
        evt.type === 'turn.started' ||
        evt.type === 'turn.completed' ||
        evt.type === 'item.started'
      ) {
        continue;
      }
    } catch {
      out.push(line);
      continue;
    }
    out.push(line);
  }
  return out.join('\n');
}

function detectToolCallLine(line) {
  const s = line.trimStart();
  if (/^[●⬤•]\s+/.test(s)) return true;
  if (/^\$\s+\S/.test(s)) return true;
  if (/^\|\s+(Read|Write|Glob|Bash|Edit|grep|find|Fetch)\b/i.test(s)) return true;
  if (/^\[tool\]\s*/i.test(s)) return true;
  return false;
}

/**
 * Send a message using the SSE /stream endpoint and update the UI live.
 * Returns the `done` event payload {response, runtime, model} on success.
 * Throws on network/HTTP error so the caller can fall back gracefully.
 */
async function sendMessageStreaming(query, sessionId) {
  const headers = { 'Content-Type': 'application/json' };
  if (STATE.token) headers['Authorization'] = `Bearer ${STATE.token}`;

  // Create AbortController so /cancel can abort this fetch
  const abortController = new AbortController();
  STATE.currentAbortController = abortController;

  const res = await fetch(`${API_BASE}/sessions/${sessionId}/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ query }),
    signal: abortController.signal,
  });

  if (!res.ok) throw new Error(`Stream request failed: HTTP ${res.status}`);

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer    = '';

  // Placeholder bubble that we fill in progressively
  let streamRow    = null;
  let streamBubble = null;
  let rawText      = '';
  let activeStreamTools = {};

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
            // Concatenate text directly — newlines are already embedded in the deltas
            const chunkText = normalizeCodexStreamText(evt.text);
            if (!chunkText) continue;
            rawText += chunkText;

            // Show formatted text while streaming so it feels instant
            streamBubble.classList.add('streaming');
            let formatted = rawText
              .replace(/</g, '&lt;')
              .replace(/>/g, '&gt;')
              .replace(/\n\n+/g, '</p><p>')  // paragraph breaks
              .replace(/\n/g, '<br>');      // line breaks
            streamBubble.innerHTML = formatted ? `<p>${formatted}</p>` : '';
            scrollToBottom();

          } else if (evt.type === 'tool_call') {
            // Tool call — interleaved block in messages thread
            const evtKind = evt.event || 'detected';
            const toolName = evt.name || 'Tool';
            const key = evt.id || toolName;
            if (evtKind === 'input_complete' || evtKind === 'start') {
              if (!activeStreamTools[key]) {
                activeStreamTools[key] = toolName;
                insertToolCallBlock(streamBubble, key, toolName, getToolInputSummary(toolName, evt.input));
              } else if (evtKind === 'input_complete' && evt.input) {
                // Update input summary if we get it later
                const row = document.getElementById('tc-' + key);
                if (row) {
                  let inp = row.querySelector('.tc-input');
                  const summary = getToolInputSummary(toolName, evt.input);
                  if (summary) {
                    if (!inp) {
                      inp = document.createElement('span');
                      inp.className = 'tc-input';
                      const status = row.querySelector('.tc-status');
                      status?.parentNode?.insertBefore(inp, status);
                    }
                    inp.textContent = summary;
                  }
                }
              }
            } else if (evtKind === 'detected' && !activeStreamTools[key]) {
              activeStreamTools[key] = toolName;
              insertToolCallBlock(streamBubble, key, toolName, getToolInputSummary(toolName, evt.input));
            } else if (evtKind === 'result' || evtKind === 'completed') {
              delete activeStreamTools[key];
              completeToolCallBlock(key, evt.output || '', evt.is_error || false);
            } else if (evtKind === 'started') {
              if (!activeStreamTools[key]) {
                activeStreamTools[key] = toolName;
                insertToolCallBlock(streamBubble, key, toolName, getToolInputSummary(toolName, evt.input));
              }
            }

          } else if (evt.type === 'done') {
            // Stop all remaining tool spinners
            cleanupAllToolSpinners();
            // Calculate response timing
            const sendEndTime = performance.now();
            const sessionData = STATE.sessionStreams[sessionId];
            const elapsedMs = sendEndTime - (sessionData?.startTime || sendEndTime);
            const elapsedSec = elapsedMs / 1000;
            // Prefer the cleaned done payload when streamed content looks like
            // Codex transport JSONL frames; otherwise keep the accumulated text.
            const doneResponse = evt.response || '(no response)';
            const normalizedRawText = normalizeCodexStreamText(rawText);
            const finalContent = normalizedRawText.trim()
              ? normalizedRawText
              : doneResponse;
            if (streamBubble) {
              streamBubble.classList.remove('streaming');
              applyMarkdownToBubble(streamBubble, finalContent);
              // Add timing/token info (Issue #128)
              const _timingText = buildTimingText(elapsedSec, evt.wee_meta || null);
              if (_timingText) {
                const timingDiv = document.createElement('div');
                timingDiv.className = 'message-timing';
                timingDiv.innerHTML = _timingText;
                streamBubble.appendChild(timingDiv);
              }
              streamBubble.appendChild(createTtsButton(streamBubble));
              scrollToBottom();
            } else {
              // Command/no-chunk path: render fresh bubble
              await renderMessage('assistant', finalContent, [], elapsedSec, evt.wee_meta || null);
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
    STATE.currentAbortController = null;
  }
  return null;
}

/**
 * Reconnect to an active stream for a session.
 * Called when the user switches back to a session that had a running query.
 * Replays buffered chunks and continues streaming live output.
 */
async function reconnectToStream(sessionId) {
  STATE.isProcessing = true;
  showTyping();

  const headers = {};
  if (STATE.token) headers['Authorization'] = `Bearer ${STATE.token}`;

  const abortController = new AbortController();
  STATE.currentAbortController = abortController;

  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}/stream/reconnect`, {
      method: 'GET',
      headers,
      signal: abortController.signal,
    });

    // Non-streaming JSON response means nothing to reconnect to
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const data = await res.json();
      if (!data.active && !data.has_stream_buffer) {
        // Query must have finished while we were away — clean up
        STATE.isProcessing = false;
        hideTyping();
        delete STATE.sessionStreams[sessionId];
        return;
      }
    }

    if (!res.ok || !contentType.includes('text/event-stream')) {
      STATE.isProcessing = false;
      hideTyping();
      delete STATE.sessionStreams[sessionId];
      return;
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer    = '';
    let streamRow    = null;
    let streamBubble = null;
    let rawText      = '';
    let activeStreamTools = {};

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        // Verify we're still on the same session
        if (STATE.currentSessionId !== sessionId) {
          abortController.abort();
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop();

        for (const frame of frames) {
          for (const line of frame.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            const payload = line.slice(6).trim();
            if (!payload) continue;

            let evt;
            try { evt = JSON.parse(payload); } catch { continue; }

            if (evt.type === 'reconnect') {
              // Create streaming bubble for reconnected stream
              hideTyping();
              ({ row: streamRow, bubble: streamBubble } = createStreamingBubble());

            } else if (evt.type === 'chunk' && streamBubble) {
              const chunkText = normalizeCodexStreamText(evt.text);
              if (!chunkText) continue;
              rawText += chunkText;
              streamBubble.classList.add('streaming');
              let formatted = rawText
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/\n\n+/g, '</p><p>')
                .replace(/\n/g, '<br>');
              streamBubble.innerHTML = formatted ? `<p>${formatted}</p>` : '';
              scrollToBottom();

            } else if (evt.type === 'tool_call') {
              // Tool call — interleaved block in messages thread
              const evtKind = evt.event || 'detected';
              const toolName = evt.name || 'Tool';
              const key = evt.id || toolName;
              if (evtKind === 'input_complete' || evtKind === 'start') {
                if (!activeStreamTools[key]) {
                  activeStreamTools[key] = toolName;
                  insertToolCallBlock(streamBubble, key, toolName, getToolInputSummary(toolName, evt.input));
                } else if (evtKind === 'input_complete' && evt.input) {
                  const row = document.getElementById('tc-' + key);
                  if (row) {
                    let inp = row.querySelector('.tc-input');
                    const summary = getToolInputSummary(toolName, evt.input);
                    if (summary) {
                      if (!inp) {
                        inp = document.createElement('span');
                        inp.className = 'tc-input';
                        const status = row.querySelector('.tc-status');
                        status?.parentNode?.insertBefore(inp, status);
                      }
                      inp.textContent = summary;
                    }
                  }
                }
              } else if (evtKind === 'detected' && !activeStreamTools[key]) {
                activeStreamTools[key] = toolName;
                insertToolCallBlock(streamBubble, key, toolName, getToolInputSummary(toolName, evt.input));
              } else if (evtKind === 'result' || evtKind === 'completed') {
                delete activeStreamTools[key];
                completeToolCallBlock(key, evt.output || '', evt.is_error || false);
              } else if (evtKind === 'started') {
                if (!activeStreamTools[key]) {
                  activeStreamTools[key] = toolName;
                  insertToolCallBlock(streamBubble, key, toolName, getToolInputSummary(toolName, evt.input));
                }
              }

            } else if (evt.type === 'done') {
              cleanupAllToolSpinners();
              const doneResponse = evt.response || '(no response)';
              const normalizedRawText = normalizeCodexStreamText(rawText);
              const finalContent = normalizedRawText.trim()
                ? normalizedRawText
                : doneResponse;
              if (streamBubble) {
                streamBubble.classList.remove('streaming');
                applyMarkdownToBubble(streamBubble, finalContent);
                streamBubble.appendChild(createTtsButton(streamBubble));
                scrollToBottom();
              } else {
                await renderMessage('assistant', finalContent, []);
              }
              STATE.isProcessing = false;
              hideTyping();
              delete STATE.sessionStreams[sessionId];
              await fetchAndUpdateMeta(sessionId);
              await loadSessions();

              // Delayed refresh to pick up LLM-generated titles
              setTimeout(() => loadSessions(), 5000);

              // Auto-submit next queued request if any
              if (STATE.requestQueue.length > 0 && !STATE.queuePaused) {
                processNextQueue();
              }
              return;

            } else if (evt.type === 'error') {
              if (streamBubble) streamBubble.remove();
              if (streamRow)    streamRow.remove();
              STATE.isProcessing = false;
              hideTyping();
              delete STATE.sessionStreams[sessionId];
              renderSystemMessage('Stream error: ' + (evt.message || 'Unknown error'));
              return;
            }
          }
        }
      }
    } finally {
      reader.releaseLock();
      STATE.currentAbortController = null;
    }

    // Stream ended without done event — clean up
    STATE.isProcessing = false;
    hideTyping();
    delete STATE.sessionStreams[sessionId];

  } catch (err) {
    STATE.currentAbortController = null;
    if (err.name === 'AbortError') {
      // Aborted by session switch or cancel — don't show error
      const streamingBubble = document.querySelector('.streaming');
      if (streamingBubble) {
        streamingBubble.classList.remove('streaming');
      }
    } else {
      STATE.isProcessing = false;
      hideTyping();
      delete STATE.sessionStreams[sessionId];
      renderSystemMessage('Reconnect failed: ' + err.message);
    }
  }
}

/** Inject markdown+highlight into an existing bubble element. */
function applyMarkdownToBubble(bubble, content) {
  // Preserve tool call blocks before replacing innerHTML (Issue #115)
  const toolBlocks = Array.from(bubble.querySelectorAll('.tc-block'));
  const timingDiv = bubble.querySelector('.message-timing');
  try {
    bubble.innerHTML = marked.parse(content, { breaks: true });
    bubble.querySelectorAll('pre code').forEach(block => {
      if (window.hljs) hljs.highlightElement(block);
    });
  } catch (_) {
    bubble.textContent = content;
  }
  // Re-append preserved tool call blocks
  toolBlocks.forEach(block => bubble.appendChild(block));
  if (timingDiv) bubble.appendChild(timingDiv);
  // Make file paths clickable after markdown render
  if (typeof linkifyFilePaths === 'function') linkifyFilePaths(bubble);
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
  avatar.innerHTML = '<img src="/static/icon-192.png" alt="Wee" style="width:32px;height:32px;border-radius:8px;">';
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
  es.innerHTML = '<div class="empty-icon"><img src="/static/icon-192.png" alt="Wee Orchestrator" style="width:160px;height:160px;border-radius:24px;opacity:0.7;"></div><p>Start a conversation or select a session from the sidebar.</p>';
  container.appendChild(es);
}

// ─── Pagination: Load Earlier Messages ────────────────────────────────────────
function updateLoadMoreButton() {
  const container = $('messages');
  const existing = container.querySelector('.load-more-btn');
  if (existing) existing.remove();

  const sid = STATE.activeSessionId;
  const pg = STATE.pagination[sid];
  if (!pg || pg.offset <= 0) return;

  const btn = document.createElement('button');
  btn.className = 'load-more-btn btn-ghost';
  btn.textContent = '⬆ Load earlier messages';
  btn.addEventListener('click', () => loadEarlierMessages());
  container.insertBefore(btn, container.firstChild);
}

async function loadEarlierMessages() {
  const sid = STATE.activeSessionId;
  const pg = STATE.pagination[sid];
  if (!pg || pg.offset <= 0) return;

  const container = $('messages');
  const btn = container.querySelector('.load-more-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Loading…'; }

  const prevScrollHeight = container.scrollHeight;

  const newOffset = Math.max(pg.offset - 100, 0);
  const fetchLimit = pg.offset - newOffset;

  try {
    const data = await apiRequest('GET',
      `/history/sessions/${sid}/messages?limit=${fetchLimit}&offset=${newOffset}`);
    const msgs = data.messages || [];

    STATE.pagination[sid].offset = newOffset;

    // Insert messages before existing content (after load-more button or at top)
    const insertRef = btn ? btn.nextSibling : container.firstChild;
    for (const msg of msgs) {
      const row = document.createElement('div');
      row.className = `message-row ${msg.role}`;
      const avatar = document.createElement('div');
      avatar.className = 'message-avatar';
      avatar.innerHTML = msg.role === 'user' ? '👤' : '<img src="/static/icon-192.png" alt="Wee" style="width:32px;height:32px;border-radius:8px;">';
      const bubble = document.createElement('div');
      bubble.className = 'message-bubble';
      if (msg.role === 'user') {
        const esc = escHtml(msg.content);
        const highlighted = esc.replace(
          /((?:^|[ \t\n]))(\/\w+)/g,
          (_, prefix, token) => `${prefix}<span class="cmd-token">${token}</span>`
        );
        bubble.innerHTML = `<span style="white-space:pre-wrap">${highlighted}</span>`;
      } else {
        try {
          bubble.innerHTML = marked.parse(msg.content, { breaks: true });
          bubble.querySelectorAll('pre code').forEach(block => {
            if (window.hljs) hljs.highlightElement(block);
          });
        } catch (_) { bubble.textContent = msg.content; }
      }
      if (typeof linkifyFilePaths === 'function') linkifyFilePaths(bubble);
      if (msg.role === 'assistant') {
        bubble.appendChild(createTtsButton(bubble));
      }
      row.appendChild(avatar);
      row.appendChild(bubble);
      container.insertBefore(row, insertRef);
    }

    // Maintain scroll position so user doesn't jump
    const newScrollHeight = container.scrollHeight;
    container.scrollTop += (newScrollHeight - prevScrollHeight);

    updateLoadMoreButton();
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = '⬆ Load earlier messages'; }
    renderSystemMessage('Could not load earlier messages: ' + err.message);
  }
}

/**
 * Build timing/token footer text for assistant messages (Issue #128).
 * Always returns string|null — never a DocumentFragment (Issue #198).
 */
function buildTimingText(elapsedSec, weeMeta) {
  const base = elapsedSec != null ? `Generated in ${elapsedSec.toFixed(1)}s` : null;
  if (!weeMeta) {
    return base ? `⏱️ ${base}` : null;
  }
  const runtime = weeMeta.runtime || '';
  const tokens = weeMeta.tokens;
  const costLabel = weeMeta.cost_label || '';
  if (runtime === 'copilot-sdk' || costLabel === 'copilot') {
    return base ? `⏱️ ${base} · copilot request` : 'copilot request';
  }
  if (tokens != null) {
    const tokenStr = tokens.toLocaleString();
    let costStr = '';
    if (costLabel === 'local') costStr = ' · local';
    else if (costLabel === 'free') costStr = ' · free';
    else if (costLabel && costLabel.startsWith('$')) costStr = ` · ${costLabel}`;
    // Issue #160: Build tooltip with input/output breakdown
    const pTokens = weeMeta.prompt_tokens;
    const cTokens = weeMeta.completion_tokens;
    let tooltip = `${tokenStr} total tokens`;
    if (pTokens != null && cTokens != null) {
      tooltip = `Input: ${pTokens.toLocaleString()} tokens\nOutput: ${cTokens.toLocaleString()} tokens\nTotal: ${tokenStr} tokens`;
      if (costLabel && costLabel.startsWith('$')) tooltip += `\nEst. cost: ${costLabel}`;
    }
    const span = `<span title="${tooltip}">${tokenStr} tokens${costStr}</span>`;
    return base ? `⏱️ ${base} · ${span}` : span;
  }
  return base ? `⏱️ ${base}` : null;
}

async function renderMessage(role, content, files = [], timing = null, weeMeta = null) {
  hide($('empty-state'));

  const container = $('messages');
  const row = document.createElement('div');
  row.className = `message-row ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.innerHTML = role === 'user' ? '👤' : '<img src="/static/icon-192.png" alt="Wee" style="width:32px;height:32px;border-radius:8px;">';

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
      bubble.innerHTML = marked.parse(content, { breaks: true });
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

  // Make file paths clickable in all messages
  if (typeof linkifyFilePaths === 'function') linkifyFilePaths(bubble);

  // Add timing/token info (Issue #128)
  if (role === 'assistant' && (timing || weeMeta)) {
    const _rmTimingText = buildTimingText(timing, weeMeta);
    if (_rmTimingText) {
      const timingDiv = document.createElement('div');
      timingDiv.className = 'message-timing';
      timingDiv.innerHTML = _rmTimingText;
      bubble.appendChild(timingDiv);
    }
  }

  // Add TTS play button for assistant messages
  if (role === 'assistant') {
    bubble.appendChild(createTtsButton(bubble));
  }
}

function renderSystemMessage(text) {
  const container = $('messages');
  const el = document.createElement('div');
  el.style.cssText = 'text-align:center;color:var(--danger);font-size:13px;padding:8px;';
  el.textContent = text;
  container.appendChild(el);
  scrollToBottom();
}

function renderBgTaskBanner(event) {
  const container = $('messages');
  if (!container) return;
  const ok = event.status === 'completed';
  const icon = ok ? '✅' : '❌';
  const label = ok ? 'completed' : 'failed';
  const summary = (event.summary || '').slice(0, 60);
  const agent = event.agent ? ` · ${event.agent}` : '';
  const banner = document.createElement('div');
  banner.className = `bg-task-banner ${ok ? 'bg-task-ok' : 'bg-task-fail'}`;
  banner.innerHTML = `${icon} <span class="bg-task-banner-id">${escHtml(event.task_id)}</span> ${label}${agent ? ` <span class="bg-task-banner-agent">${escHtml(agent)}</span>` : ''} — <span class="bg-task-banner-summary">${escHtml(summary)}</span>`;
  container.appendChild(banner);
  scrollToBottom();
}

let _bgEventPollInterval = null;
function startBgEventPolling() {
  if (_bgEventPollInterval) return;
  _bgEventPollInterval = setInterval(pollBgEvents, 8000);
}

async function pollBgEvents() {
  const sid = STATE.currentSessionId;
  if (!sid) return;
  try {
    const data = await apiRequest('GET', `/sessions/${sid}/bg-events`);
    if (!data || !data.events || !data.events.length) return;
    for (const ev of data.events) {
      if (BG.shownBgBanners.has(ev.task_id)) continue;
      BG.shownBgBanners.add(ev.task_id);
      renderBgTaskBanner(ev);
    }
  } catch (_) { /* ignore polling errors */ }
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
    // Auto-select the most recent session so agent/runtime meta is immediately visible
    // instead of showing a blank empty-state that requires a manual click to restore.
    await selectSession(STATE.sessions[0].session_id);
  }
}

// ─── Boot ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {

  // --- Mobile viewport sizing (iOS Safari keyboard / browser chrome) ---
  updateMobileViewportVars();
  window.addEventListener('resize', updateMobileViewportVars);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', updateMobileViewportVars);
    window.visualViewport.addEventListener('scroll', updateMobileViewportVars);
  }

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
  // On mobile, sidebar starts collapsed (hidden); JS toggleSidebar uses .collapsed class
  if (isMobileViewport()) {
    document.querySelector('.sidebar').classList.add('collapsed');
  }
  $('btn-new-chat').addEventListener('click', () => {
    startNewSession();
    if (isMobileViewport()) toggleSidebar(false);
  });
  $('btn-logout').addEventListener('click', () => { clearAuth(); showAuthView(); });
  $('btn-sidebar-toggle').addEventListener('click', () => toggleSidebar(false));
  $('btn-open-sidebar').addEventListener('click',  () => toggleSidebar(true));
  $('btn-sched-open-sidebar').addEventListener('click', () => toggleSidebar(true));
  if ($('btn-bg-open-sidebar')) $('btn-bg-open-sidebar').addEventListener('click', () => toggleSidebar(true));

  // --- View nav ---
  $('btn-nav-chat').addEventListener('click', showChatPanel);
  $('btn-nav-background').addEventListener('click', showBackgroundPanel);
  $('btn-nav-scheduler').addEventListener('click', showSchedulerPanel);
  $('btn-nav-notifications').addEventListener('click', toggleNotificationPanel);

  // F027: Verbose mode toggle click handler
  $('btn-verbose-toggle')?.addEventListener('click', async () => {
    if (!STATE.currentSessionId) return;
    const newSilent = !STATE.silentMode;
    STATE.silentMode = newSilent;
    _updateVerboseToggleUI(newSilent);
    localStorage.setItem(
      'wee_verbose_' + STATE.currentSessionId,
      newSilent ? '0' : '1'
    );
    try {
      await apiRequest(
        'PATCH',
        '/sessions/' + STATE.currentSessionId + '/settings',
        { silent_mode: newSilent }
      );
    } catch (_) { /* non-fatal — UI already updated */ }
  });

  // Notification settings/actions
  $('btn-notif-mark-all-read').addEventListener('click', async () => {
    try {
      await apiRequest('POST', '/notifications/read-all');
      await pollNotifications();
    } catch { /* ignore */ }
  });
  $('btn-notif-clear-read').addEventListener('click', async () => {
    try {
      await apiRequest('DELETE', '/notifications');
      await pollNotifications();
    } catch { /* ignore */ }
  });
  $('btn-notif-settings').addEventListener('click', () => {
    const bar = $('notif-settings-bar');
    bar.classList.toggle('hidden');
  });

  // --- Keyring unlock listeners (Issue #93) ---
  _initKeyringListeners();

  // --- Secrets panel listeners (F019) ---
  if ($('btn-nav-secrets')) $('btn-nav-secrets').addEventListener('click', showSecretsPanel);
  if ($('btn-secrets-open-sidebar')) $('btn-secrets-open-sidebar').addEventListener('click', () => toggleSidebar(true));
  $('btn-secrets-refresh').addEventListener('click', loadSecrets);
  $('btn-secrets-save').addEventListener('click', saveSecret);
  $('btn-secrets-clear').addEventListener('click', () => {
    $('secret-name-input').value = '';
    $('secret-value-input').value = '';
    hide($('secrets-form-feedback'));
  });
  $("btn-secrets-toggle-vis").addEventListener("click", () => {
    const inp = $("secret-value-input");
    const isHidden = inp.type === "password";
    inp.type = isHidden ? "text" : "password";
    const closedIcon = $("btn-secrets-toggle-vis").querySelector(".eye-closed");
    const openIcon = $("btn-secrets-toggle-vis").querySelector(".eye-open");
    if (closedIcon && openIcon) {
      closedIcon.classList.toggle("hidden", isHidden);
      openIcon.classList.toggle("hidden", !isHidden);
    }
  });
  $('secret-name-input').addEventListener('keydown', e => { if (e.key === 'Enter') $('secret-value-input').focus(); });
  $('secret-value-input').addEventListener('keydown', e => { if (e.key === 'Enter') saveSecret(); });

  const notifToggle = $('notif-enabled-toggle');
  notifToggle.checked = isNotificationsEnabled();
  notifToggle.addEventListener('change', () => {
    setNotificationsEnabled(notifToggle.checked);
  });
  // Sync toggle state from backend on page load (Issue #146)
  syncNotificationToggleFromBackend();

  // --- Request Queue ---
  const btnToggleQueue = $('btn-toggle-queue');
  if (btnToggleQueue) {
    btnToggleQueue.addEventListener('click', toggleQueuePanel);
  }
  const btnPauseQueue = $('btn-pause-queue');
  if (btnPauseQueue) {
    btnPauseQueue.addEventListener('click', toggleQueuePause);
  }
  
  // --- Queue Section (Collapsible) ---
  const btnToggleQueueSection = $('btn-toggle-queue-section');
  if (btnToggleQueueSection) {
    btnToggleQueueSection.addEventListener('click', toggleQueueSection);
  }
  
  // --- Scratch Notes ---
  const scratchTextarea = $('scratch-textarea');
  if (scratchTextarea) {
    // Load scratch notes from session state
    loadScratchNotes();
    
    // Save on input with debounce
    let scratchSaveTimer;
    scratchTextarea.addEventListener('input', (e) => {
      // Update char count
      const charCount = e.target.value.length;
      const charCountEl = $('scratch-char-count');
      if (charCountEl) charCountEl.textContent = charCount;
      
      // Debounce save
      clearTimeout(scratchSaveTimer);
      scratchSaveTimer = setTimeout(() => {
        saveScratchNotes(e.target.value);
      }, 500);
    });
  }


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
    // Small delay so (mouse|touch) on dropdown fires first
    setTimeout(() => hideCommandDropdown(), 150);
  });

  ta.addEventListener('focus', () => {
    if (isMobileViewport()) setTimeout(scrollToBottom, 50);
  });

  // --- Send button ---
  $('btn-send').addEventListener('click', sendMessage);

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

  // Clicking the chat messages area collapses the notification panel
  $('messages').addEventListener('click', () => {
    if (!$('notification-panel').classList.contains('notif-hidden')) {
      hideNotificationPanel();
    }
  });

  // --- Meta pill popovers ---
  ['meta-agent', 'meta-runtime', 'meta-model', 'meta-permissions'].forEach(id => {
    $(id).addEventListener('click', e => { e.stopPropagation(); showPillPopover($(id), id); });
  });
  document.addEventListener('mousedown', e => {
    if (_pillPopover && !_pillPopover.contains(e.target)) hidePillPopover();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hidePillPopover(); });

  // --- Ctrl+C global shortcut: cancel the running request ---
  document.addEventListener('keydown', async e => {
    if (!e.ctrlKey || e.key !== 'c') return;
    // Only intercept when focus is outside any text input/textarea/contenteditable
    const active = document.activeElement || document.body;
    const tag = active.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || active.isContentEditable) return;
    if (!STATE.isProcessing || !STATE.currentSessionId) return;

    e.preventDefault();

    // Abort the in-flight streaming fetch
    if (STATE.currentAbortController) {
      STATE.currentAbortController.abort();
      STATE.currentAbortController = null;
    }

    // Call the dedicated cancel endpoint
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (STATE.token) headers['Authorization'] = `Bearer ${STATE.token}`;
      const res = await fetch(`${API_BASE}/sessions/${STATE.currentSessionId}/cancel`, {
        method: 'POST',
        headers,
      });
      const data = await res.json();
      renderSystemMessage(data.cancelled ? `✓ ${data.message}` : `ℹ️ ${data.message}`);
    } catch (err) {
      renderSystemMessage('❌ Failed to cancel: ' + err.message);
    }

    // Clean up processing state
    hideTyping();
    STATE.isProcessing = false;
    delete STATE.sessionStreams[STATE.currentSessionId];
    $('btn-send').disabled = false;
    scrollToBottom();

    // Brief visual feedback
    schedToast('⌨️ Ctrl+C — request cancelled', 'error');
  });

  // --- Scheduler UI events ---
  $('btn-sched-refresh').addEventListener('click', () => loadSchedulerJobs(true));
  $('btn-sched-new').addEventListener('click', openNewJobForm);

  // --- Background Tasks UI events ---
  $('btn-bg-refresh').addEventListener('click', () => loadBackgroundTasks(true));

  // --- Bootstrap ---
  // Fetch feature flags first (no auth needed) then decide what to show
  fetch('/api/v1/config')
    .then(r => r.json())
    .then(cfg => {
      STATE.schedulerEnabled = cfg.scheduler_enabled !== false;
      STATE.bgTasksEnabled = cfg.background_tasks_enabled !== false;
    })
    .catch(() => { /* keep defaults */ })
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
  hide($('secrets-panel'));
  hide($('scheduler-panel'));
  hide($('background-panel'));
  show($('btn-new-chat'));
  show($('sessions-list'));
  hide($('bg-sidebar-list'));
  hide($('sched-sidebar-list'));
  show($('request-queue-panel'));
  $('btn-nav-chat').classList.add('active');
  $('btn-nav-scheduler').classList.remove('active');
  $('btn-nav-background').classList.remove('active');
  $('btn-nav-notifications').classList.remove('active');
  $('btn-nav-secrets').classList.remove('active');
  hideNotificationPanel();
  if (isMobileViewport()) toggleSidebar(false);
}

function showSchedulerPanel() {
  hide($('secrets-panel'));
  hide($('chat-panel'));
  show($('scheduler-panel'));
  hide($('background-panel'));
  hide($('btn-new-chat'));
  hide($('sessions-list'));
  hide($('bg-sidebar-list'));
  show($('sched-sidebar-list'));
  hide($('request-queue-panel'));
  $('btn-nav-scheduler').classList.add('active');
  $('btn-nav-chat').classList.remove('active');
  $('btn-nav-background').classList.remove('active');
  $('btn-nav-notifications').classList.remove('active');
  $('btn-nav-secrets').classList.remove('active');
  hideNotificationPanel();
  loadSchedulerJobs();
  loadSchedulerStatus();
  // On mobile, keep sidebar open so job list is visible immediately
  if (isMobileViewport()) toggleSidebar(true);
}

function showBackgroundPanel() {
  hide($('secrets-panel'));
  hide($('chat-panel'));
  hide($('scheduler-panel'));
  show($('background-panel'));
  hide($('btn-new-chat'));
  hide($('sessions-list'));
  show($('bg-sidebar-list'));
  hide($('sched-sidebar-list'));
  hide($('request-queue-panel'));
  $('btn-nav-background').classList.add('active');
  $('btn-nav-chat').classList.remove('active');
  $('btn-nav-scheduler').classList.remove('active');
  $('btn-nav-notifications').classList.remove('active');
  $('btn-nav-secrets').classList.remove('active');
  hideNotificationPanel();
  loadBackgroundTasks();
  // On mobile, keep sidebar open so task list is visible immediately
  if (isMobileViewport()) toggleSidebar(true);
}


// ═══════════════════════════════════════════════════════════════════════════════
// ─── Secrets Manager Panel (F019) — Redesigned ──────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

function showSecretsPanel() {
  hide($('chat-panel'));
  hide($('scheduler-panel'));
  hide($('background-panel'));
  show($('secrets-panel'));
  hide($('btn-new-chat'));
  hide($('sessions-list'));
  hide($('bg-sidebar-list'));
  hide($('sched-sidebar-list'));
  hide($('request-queue-panel'));
  $('btn-nav-secrets').classList.add('active');
  $('btn-nav-chat').classList.remove('active');
  $('btn-nav-background').classList.remove('active');
  $('btn-nav-scheduler').classList.remove('active');
  $('btn-nav-notifications').classList.remove('active');
  hideNotificationPanel();
  checkKeyringStatus();
  loadSecrets();
  if (isMobileViewport()) toggleSidebar(false);
}

async function loadSecrets() {
  const listEl = $('secrets-list');
  if (!listEl) return;
  listEl.innerHTML = '<div class="secrets-loading"><span class="secrets-loading-dot"></span><span class="secrets-loading-dot"></span><span class="secrets-loading-dot"></span></div>';
  try {
    const data = await apiRequest('GET', '/secrets');
    const names = data.secrets || [];
    // Update count badge
    const badge = $('secrets-count-badge');
    if (badge) badge.textContent = names.length + (names.length === 1 ? ' secret' : ' secrets');
    if (names.length === 0) {
      listEl.innerHTML = `
        <div class="secrets-empty-state">
          <div class="secrets-empty-icon">🔒</div>
          <p class="secrets-empty-text">No secrets stored yet</p>
          <p class="secrets-empty-hint">Use the form to securely store your first credential</p>
        </div>`;
      return;
    }
    listEl.innerHTML = names.map(name => `
      <div class="secrets-item">
        <div class="secrets-item-left">
          <span class="secrets-item-icon">🔑</span>
          <span class="secrets-item-name">${escapeHtml(name)}</span>
        </div>
        <div class="secrets-item-actions">
          <button class="secrets-edit-btn" data-name="${escapeHtml(name)}" title="Edit / rotate value">✏️</button>
          <button class="secrets-delete-btn" data-name="${escapeHtml(name)}" title="Delete secret">✕</button>
        </div>
      </div>
    `).join('');
    listEl.querySelectorAll('.secrets-delete-btn').forEach(btn => {
      btn.addEventListener('click', () => deleteSecret(btn.dataset.name));
    });
    listEl.querySelectorAll('.secrets-edit-btn').forEach(btn => {
      btn.addEventListener('click', () => editSecret(btn.dataset.name));
    });
  } catch (err) {
    const msg = err.message || '';
    if (/locked|keyring|unlock/i.test(msg)) {
      listEl.innerHTML = `<div class="secrets-empty-state"><div class="secrets-empty-icon">🔒</div><p class="secrets-empty-text secrets-error">Secret store is locked</p><p class="secrets-empty-hint">Click <strong>Unlock</strong> above to enter the keyring password.</p></div>`;
      checkKeyringStatus();
    } else {
      listEl.innerHTML = `<div class="secrets-empty-state"><p class="secrets-empty-text secrets-error">⚠ ${escapeHtml(msg)}</p></div>`;
    }
  }
}

function editSecret(name) {
  // Collapse any other open edit forms first
  document.querySelectorAll('.secrets-edit-inline').forEach(el => el.remove());
  document.querySelectorAll('.secrets-item--editing').forEach(el => el.classList.remove('secrets-item--editing'));

  const item = document.querySelector(`.secrets-edit-btn[data-name="${CSS.escape(name)}"]`);
  if (!item) return;
  const row = item.closest('.secrets-item');
  if (!row) return;
  row.classList.add('secrets-item--editing');

  const form = document.createElement('div');
  form.className = 'secrets-edit-inline';
  form.innerHTML = `
    <div class="secrets-edit-row">
      <span class="secrets-edit-label">Rotate value for <strong>${escapeHtml(name)}</strong></span>
      <div class="secrets-edit-input-wrap">
        <input type="password" class="secrets-input glass-input secrets-edit-value" placeholder="Enter new value\u2026" autocomplete="off" />
      </div>
      <div class="secrets-edit-actions">
        <button class="btn btn-primary btn-sm secrets-edit-save" type="button">Save</button>
        <button class="btn btn-ghost btn-sm secrets-edit-cancel" type="button">Cancel</button>
      </div>
    </div>`;
  row.after(form);

  const inp = form.querySelector('.secrets-edit-value');
  inp.focus();
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') submitEditSecret(name, inp.value);
    if (e.key === 'Escape') cancelEditSecret(row, form);
  });
  form.querySelector('.secrets-edit-save').addEventListener('click', () => submitEditSecret(name, inp.value));
  form.querySelector('.secrets-edit-cancel').addEventListener('click', () => cancelEditSecret(row, form));
}

async function submitEditSecret(name, value) {
  if (!value) { showSecretsFeedback('New value is required', 'error'); return; }
  try {
    await apiRequest('POST', '/secrets', { name, value });
    showSecretsFeedback(`✅ Rotated secret "${escapeHtml(name)}"`, 'success');
    loadSecrets();
  } catch (err) {
    showSecretsFeedback(`❌ ${err.message}`, 'error');
  }
}

function cancelEditSecret(row, form) {
  row.classList.remove('secrets-item--editing');
  form.remove();
}

async function saveSecret() {
  const nameEl = $('secret-name-input');
  const valueEl = $('secret-value-input');
  const fb = $('secrets-form-feedback');
  const name = (nameEl.value || '').trim();
  const value = valueEl.value || '';
  if (!name) { showSecretsFeedback('Name is required', 'error'); nameEl.focus(); return; }
  if (!value) { showSecretsFeedback('Value is required', 'error'); valueEl.focus(); return; }
  if (!/^[A-Za-z0-9._-]+$/.test(name)) {
    showSecretsFeedback('Name may only contain letters, digits, hyphens, underscores, dots', 'error');
    nameEl.focus();
    return;
  }
  try {
    const result = await apiRequest('POST', '/secrets', { name, value });
    const action = result.action === 'updated' ? 'Updated' : 'Created';
    showSecretsFeedback(`✅ ${action} secret "${name}"`, 'success');
    nameEl.value = '';
    valueEl.value = '';
    loadSecrets();
  } catch (err) {
    showSecretsFeedback(`❌ ${err.message}`, 'error');
  }
}

async function deleteSecret(name) {
  if (!confirm(`Delete secret "${name}"? This cannot be undone.`)) return;
  try {
    await apiRequest('DELETE', `/secrets/${encodeURIComponent(name)}`);
    showSecretsFeedback(`✅ Deleted "${name}"`, 'success');
    loadSecrets();
  } catch (err) {
    showSecretsFeedback(`❌ ${err.message}`, 'error');
  }
}

function showSecretsFeedback(msg, type) {
  const fb = $('secrets-form-feedback');
  if (!fb) return;
  fb.textContent = msg;
  fb.className = 'secrets-feedback secrets-feedback--' + type;
  show(fb);
  clearTimeout(fb._timer);
  fb._timer = setTimeout(() => hide(fb), 5000);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Keyring Status & Unlock (Issue #93) ─────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

let _keyringStatus = null;

async function checkKeyringStatus() {
  const banner = $('keyring-status-banner');
  if (!banner) return;
  try {
    const data = await apiRequest('GET', '/secrets/keyring-status');
    _keyringStatus = data.status;
    if (data.status === 'locked') {
      $('keyring-banner-title').textContent = 'Secret store is locked';
      $('keyring-banner-detail').textContent = data.message || 'Unlock the keyring to access secrets.';
      banner.className = 'keyring-banner keyring-banner--locked';
      show(banner);
    } else if (data.status === 'unavailable') {
      $('keyring-banner-title').textContent = 'Secret store unavailable';
      $('keyring-banner-detail').textContent = data.message || 'No keyring service detected.';
      banner.className = 'keyring-banner keyring-banner--warn';
      const ub = banner.querySelector('.keyring-unlock-btn');
      if (ub) ub.style.display = 'none';
      show(banner);
    } else {
      hide(banner);
    }
  } catch (_) {
    hide(banner);
  }
}

function showKeyringUnlockDialog() {
  const dialog = $('keyring-unlock-dialog');
  if (!dialog) return;
  show(dialog);
  const inp = $('keyring-password-input');
  inp.value = '';
  hide($('keyring-unlock-feedback'));
  setTimeout(() => inp.focus(), 100);
}

function hideKeyringUnlockDialog() {
  const dialog = $('keyring-unlock-dialog');
  if (dialog) hide(dialog);
}

function showKeyringFeedback(msg, type) {
  const fb = $('keyring-unlock-feedback');
  if (!fb) return;
  fb.textContent = msg;
  fb.className = 'keyring-dialog-feedback keyring-dialog-feedback--' + type;
  show(fb);
}

async function submitKeyringUnlock() {
  const password = ($('keyring-password-input').value || '').trim();
  if (!password) {
    showKeyringFeedback('Password is required', 'error');
    return;
  }
  const btn = $('btn-keyring-submit');
  btn.disabled = true;
  btn.textContent = 'Unlocking…';
  showKeyringFeedback('Attempting to unlock…', 'info');
  try {
    await apiRequest('POST', '/secrets/keyring-unlock', { password });
    showKeyringFeedback('✅ Keyring unlocked successfully!', 'success');
    setTimeout(() => {
      hideKeyringUnlockDialog();
      checkKeyringStatus();
      loadSecrets();
    }, 800);
  } catch (err) {
    showKeyringFeedback('❌ ' + (err.message || 'Unlock failed'), 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Unlock';
  }
}

function _initKeyringListeners() {
  const unlockBtn = $('btn-keyring-unlock');
  if (unlockBtn) unlockBtn.addEventListener('click', showKeyringUnlockDialog);
  const submitBtn = $('btn-keyring-submit');
  if (submitBtn) submitBtn.addEventListener('click', submitKeyringUnlock);
  const cancelBtn = $('btn-keyring-cancel');
  if (cancelBtn) cancelBtn.addEventListener('click', hideKeyringUnlockDialog);
  const pwdInput = $('keyring-password-input');
  if (pwdInput) {
    pwdInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') submitKeyringUnlock();
      if (e.key === 'Escape') hideKeyringUnlockDialog();
    });
  }
  const overlay = $('keyring-unlock-dialog');
  if (overlay) {
    overlay.addEventListener('click', e => {
      if (e.target === overlay) hideKeyringUnlockDialog();
    });
  }
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
  try {
    const data = await schedApi('GET', '/jobs');
    SCHED.jobs = data.result || [];
    renderSchedJobsSidebar();
    if (showToast) schedToast('Jobs refreshed', 'success');
  } catch (err) {
    const list = $('sched-sidebar-list');
    if (list) list.innerHTML = '<p class="sched-empty sched-error" style="padding:24px 12px;text-align:center;">Failed to load jobs: ' + escHtml(err.message) + '</p>';
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

function renderSchedJobsSidebar() {
  const list = $('sched-sidebar-list');
  if (!list) return;
  if (!SCHED.jobs.length) {
    list.innerHTML = '<p class="sched-empty" style="padding:24px 12px;text-align:center;color:var(--text-muted);font-size:13px;">No scheduled jobs yet.<br>Click <strong>+ New Job</strong> to create one.</p>';
    return;
  }

  const icons = { true: '🟢', false: '🟡' };
  list.innerHTML = SCHED.jobs.map(job => {
    const icon = icons[String(job.enabled)] || '❓';
    const active = job.id === SCHED.selectedJobId ? 'active' : '';
    const nextRun = job.next_run ? fmtDate(job.next_run) : '—';
    const name = escHtml(job.name || job.id);
    const sched = escHtml(job.schedule || '');
    return `
      <div class="session-item sched-sidebar-item ${active}" onclick="selectSchedJob('${job.id}')">
        <div class="session-title">${icon} ${name}</div>
        <div class="session-preview">${sched} · Next: ${nextRun}</div>
      </div>`;
  }).join('');
}

// Keep backward compat alias
function renderSchedulerJobs() { renderSchedJobsSidebar(); }

window.selectSchedJob = function(jobId) {
  const job = SCHED.jobs.find(j => j.id === jobId);
  if (!job) return;
  SCHED.selectedJobId = jobId;
  renderSchedJobsSidebar();
  openJobDetail(jobId);
  // On mobile, close sidebar to show detail panel
  if (isMobileViewport()) toggleSidebar(false);
};

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
  $('sched-detail-body').innerHTML = '<p class="sched-detail-empty">Select a job from the sidebar to view details</p>';
  renderSchedJobsSidebar();
}

async function openJobDetail(jobId) {
  SCHED.selectedJobId = jobId;
  const job = SCHED.jobs.find(j => j.id === jobId);
  if (!job) return;

  const body = $('sched-detail-body');
  body.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">Loading…</p>';

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
        <dt>Type</dt>     <dd>${job.mode === 'command' ? '⚙️ Command' : '🤖 AI'}</dd>
        <dt>Schedule</dt> <dd>${escHtml(job.schedule)}${job.cron ? ' \u2192 <code>' + escHtml(job.cron) + '</code>' : ''}</dd>
        ${job.mode !== 'command' ? `
        <dt>Agent</dt>    <dd>${escHtml(job.agent)}</dd>
        <dt>Runtime</dt>  <dd>${runtimeIconHTML(job.runtime)}${escHtml(job.runtime)}</dd>
        ` : `
        <dt>Working Dir</dt> <dd>${escHtml(job.working_dir || '/opt')}</dd>
        `}
        <dt>Recurring</dt><dd>${job.recurring ? 'Yes' : 'No (one-shot)'}</dd>
        <dt>Notify</dt>   <dd>${job.notify ? 'Yes (Telegram)' : 'No'}</dd>
        <dt>Timeout</dt>  <dd>${job.timeout ? `${job.timeout}s (${fmtTimeout(job.timeout)})` : '300s (5 minutes)'}</dd>
        <dt>Next run</dt> <dd>${escHtml(job.next_run ? fmtDate(job.next_run) : '—')}</dd>
        <dt>Last run</dt> <dd>${escHtml(job.last_run ? fmtDate(job.last_run) : 'never')}</dd>
        <dt>Created</dt>  <dd>${escHtml(job.created_at ? fmtDate(job.created_at) : '—')}</dd>
      </dl>
      <div class="sched-task-box">
        <label>${job.mode === 'command' ? 'Command' : 'Task prompt'}</label>
        <pre class="sched-task-pre">${escHtml(job.task || '(empty)')}</pre>
      </div>
      <div class="sched-detail-actions">
        <button class="btn btn-accent btn-sm" id="btn-job-run-now">▶ Run Now</button>
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
  const runNowBtn  = body.querySelector('#btn-job-run-now');
  const pauseBtn   = body.querySelector('#btn-job-pause');
  const resumeBtn  = body.querySelector('#btn-job-resume');
  const deleteBtn  = body.querySelector('#btn-job-delete');

  if (runNowBtn) runNowBtn.addEventListener('click', () => doJobRunNow(job.id, runNowBtn));
  if (pauseBtn)  pauseBtn.addEventListener('click',  () => doJobPause(job.id));
  if (resumeBtn) resumeBtn.addEventListener('click', () => doJobResume(job.id));
  if (deleteBtn) deleteBtn.addEventListener('click', () => doJobDelete(job.id));
}

function renderJobEditForm(job, container) {
  container.innerHTML = buildJobForm(job);
  populateAgentDropdown(container);
  populateRuntimeDropdown(container).then(() => {
    const rt = container.querySelector('select[name="runtime"]');
    populateModelDropdown(container, rt?.value || 'claude');
    if (rt) rt.addEventListener('change', () => populateModelDropdown(container, rt.value));
  });
  wireJobForm(container, async (payload) => {
    try {
      await schedApi('PUT', `/jobs/${job.id}`, payload);
      schedToast('Job updated', 'success');
      await loadSchedulerJobs();
      const updated = SCHED.jobs.find(j => j.id === job.id);
      if (updated) {
        renderJobDetailView(updated);
      }
    } catch (err) {
      schedToast('Update failed: ' + err.message, 'error');
    }
  });
  // M-1: pre-populate fallback fields when editing an existing job
  const fbRtEl = document.getElementById('sched-fallback-runtime');
  const fbModelEl = document.getElementById('sched-fallback-model');
  if (fbRtEl && job && job.fallback_runtime) fbRtEl.value = job.fallback_runtime;
  if (fbModelEl) populateFallbackModelDropdown(fbModelEl, fbRtEl?.value || '', job?.fallback_model || '');
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Scheduler: New Job Form ─────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

function openNewJobForm() {
  SCHED.selectedJobId = null;
  renderSchedJobsSidebar();
  const body = $('sched-detail-body');
  body.innerHTML = buildJobForm(null);
  populateAgentDropdown(body);
  populateRuntimeDropdown(body).then(() => {
    const rt = body.querySelector('select[name="runtime"]');
    populateModelDropdown(body, rt?.value || 'claude');
    if (rt) rt.addEventListener('change', () => populateModelDropdown(body, rt.value));
  });
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
}

function fmtTimeout(secs) {
  const s = parseInt(secs, 10);
  if (!s || isNaN(s)) return '';
  if (s < 60) return `${s}s`;
  if (s < 3600) {
    const m = Math.round(s / 60);
    return m === 1 ? '1 minute' : `${m} minutes`;
  }
  const h = (s / 3600).toFixed(1).replace(/\.0$/, '');
  return h === '1' ? '1 hour' : `${h} hours`;
}

function buildJobForm(job) {
  const v = (field, fallback = '') => escHtml(job?.[field] ?? fallback);
  const checked = (field, fallback = false) => (job?.[field] ?? fallback) ? 'checked' : '';
  const isCmd = job?.mode === 'command';
  const timeoutVal = job?.timeout ?? 300;
  const timeoutDisplay = fmtTimeout(timeoutVal);
  return `
    <form class="sched-form" id="sched-job-form">
      <div class="form-group">
        <label>Name <span class="req">*</span></label>
        <input class="glass-input" name="name" value="${v('name')}" placeholder="Daily summary" required />
      </div>
      <div class="form-group">
        <span>Task Type:</span>
        <div class="mode-toggle" id="sched-mode-toggle">
          <button type="button" class="mode-toggle-btn${isCmd ? '' : ' active'}" data-mode="ai">🤖 AI</button>
          <button type="button" class="mode-toggle-btn${isCmd ? ' active' : ''}" data-mode="command">⚙️ Command</button>
        </div>
        <input type="hidden" name="exec_mode" value="${isCmd ? 'command' : 'ai'}" />
      </div>
      <div class="form-group">
        <label>Schedule <span class="req">*</span></label>
        <input class="glass-input" name="schedule" id="sched-schedule-input" value="${v('schedule')}" placeholder="every day at 9am" required />
        <p class="form-hint">e.g. "in 5 minutes", "every day at 9am", "every Monday at 8am", "every 6 hours", or cron: "0 9 * * 1-5"</p>
        <div id="sched-cron-preview" class="sched-cron-preview hidden"></div>
        ${job?.cron ? '<div class="sched-cron-preview sched-cron-saved"><span class="cron-badge">cron</span> <code>' + escHtml(job.cron) + '</code></div>' : ''}
      </div>
      <div id="sched-ai-fields" class="${isCmd ? 'hidden' : ''}">
        <div class="form-row">
          <div class="form-group">
            <label>Agent</label>
            <select class="glass-input glass-select" name="agent" data-current="${escHtml(job?.agent ?? '')}">
              <option value="">Loading agents…</option>
            </select>
          </div>
          <div class="form-group">
            <label>Runtime</label>
            <select class="glass-input glass-select" name="runtime" data-current="${escHtml(job?.runtime ?? 'claude')}">
              <option value="">Loading runtimes…</option>
            </select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Model <small class="form-hint-inline">(optional — leave blank for runtime default)</small></label>
            <select class="glass-input glass-select" name="model" data-current="${escHtml(job?.model ?? '')}">
              <option value="">Loading models…</option>
            </select>
          </div>
          <div class="form-group">
            <label>Mode</label>
            <select class="glass-input glass-select" name="mode">
              <option value="restricted" ${(job?.mode !== 'elevated' && job?.mode !== 'sandboxed') ? 'selected' : ''}>restricted (safe)</option>
              <option value="elevated"   ${job?.mode === 'elevated' ? 'selected' : ''}>elevated (full access)</option>
              <option value="sandboxed"  ${job?.mode === 'sandboxed' ? 'selected' : ''}>sandboxed (read-only)</option>
            </select>
          </div>
        </div>
      </div>
      <div id="sched-cmd-fields" class="${isCmd ? '' : 'hidden'}">
        <div class="form-group">
          <label>Working Directory</label>
          <input class="glass-input" name="working_dir" value="${v('working_dir', '/opt')}" placeholder="/opt" />
        </div>
      </div>
      <div class="form-group">
        <label id="sched-task-label">${isCmd ? 'Command' : 'Task prompt'} <span class="req">*</span></label>
        <textarea class="glass-input sched-task-input" name="task" rows="4" placeholder="${isCmd ? 'Shell command to execute…' : 'Describe the task the agent should perform…'}" required>${v('task')}</textarea>
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
      <div class="form-group">
        <label>Timeout (seconds)</label>
        <div class="timeout-input-row">
          <input class="glass-input timeout-input" type="number" name="timeout" value="${timeoutVal}" min="60" max="3600" step="30" />
          <span class="timeout-display" id="sched-timeout-display">${timeoutDisplay ? `= ${timeoutDisplay}` : ''}</span>
        </div>
        <p class="form-hint">Default is 300 seconds (5 minutes). Min: 60s, Max: 3600s (1 hour).</p>
      </div>
      <details class="form-group" style="margin-top:8px">
        <summary style="cursor:pointer;font-weight:600;color:var(--text-secondary,#aaa)">
          ▶ Fallback Configuration
        </summary>
        <div style="margin-top:8px">
          <div class="form-group">
            <label>Fallback Runtime</label>
            <select class="glass-input glass-select" id="sched-fallback-runtime" name="fallback_runtime">
              <option value="">None (no fallback)</option>
            </select>
            <small>Used if primary runtime fails (rate limit, auth error, timeout)</small>
          </div>
          <div class="form-group">
            <label>Fallback Model</label>
            <select class="glass-input glass-select" id="sched-fallback-model" name="fallback_model">
              <option value="">None (no fallback)</option>
            </select>
            <small>Used with fallback runtime</small>
          </div>
        </div>
      </details>
      <div class="sched-form-actions">
        <button type="submit" class="btn btn-primary">💾 Save</button>
        <button type="button" class="btn btn-ghost" id="btn-form-cancel">Cancel</button>
      </div>
      <p id="sched-form-error" class="auth-error hidden"></p>
    </form>
  `;
}

async function populateAgentDropdown(container) {
  const select = container.querySelector('select[name="agent"]');
  if (!select) return;
  const current = select.dataset.current;
  try {
    const data = await apiRequest('GET', '/agents');
    const agents = data.agents || [];
    select.innerHTML = agents.map(a => {
      const sel = a.name === current || (!current && a.name === 'orchestrator') ? ' selected' : '';
      const label = a.description ? `${a.name} — ${a.description}` : a.name;
      return `<option value="${escHtml(a.name)}"${sel}>${escHtml(label)}</option>`;
    }).join('');
    if (!agents.length) {
      select.innerHTML = '<option value="orchestrator">orchestrator</option>';
    }
  } catch (e) {
    select.innerHTML = current
      ? `<option value="${escHtml(current)}" selected>${escHtml(current)}</option>`
      : '<option value="orchestrator">orchestrator</option>';
  }
}

async function populateRuntimeDropdown(container) {
  const select = container.querySelector('select[name="runtime"]');
  if (!select) return;
  const current = select.dataset.current || 'claude';
  try {
    const data = await apiRequest('GET', '/runtimes');
    const runtimes = data.runtimes || [];
    select.innerHTML = runtimes.map(r => {
      const sel = r.id === current ? ' selected' : '';
      return `<option value="${escHtml(r.id)}"${sel}>${escHtml(r.label)}</option>`;
    }).join('');
    if (!runtimes.length) {
      select.innerHTML = '<option value="claude">claude</option>';
    }
  } catch (e) {
    select.innerHTML = `<option value="${escHtml(current)}" selected>${escHtml(current)}</option>`;
  }
}

async function populateModelDropdown(container, runtime) {
  const select = container.querySelector('select[name="model"]');
  if (!select) return;
  const current = select.dataset.current || '';
  try {
    const data = await apiRequest('GET', `/models?runtime=${encodeURIComponent(runtime)}`);
    const models = data.models || [];
    let opts = '<option value="">(runtime default)</option>';
    // Group models by their group field for optgroup rendering
    const groups = {};
    let hasGroups = false;
    models.forEach(m => {
      const g = m.group || '';
      if (g) hasGroups = true;
      if (!groups[g]) groups[g] = [];
      groups[g].push(m);
    });
    if (hasGroups) {
      for (const [gName, gModels] of Object.entries(groups)) {
        if (gName) opts += `<optgroup label="${escHtml(gName)}">`;
        opts += gModels.map(m => {
          const sel = m.id === current ? ' selected' : '';
          return `<option value="${escHtml(m.id)}"${sel}>${escHtml(m.label)}</option>`;
        }).join('');
        if (gName) opts += '</optgroup>';
      }
    } else {
      opts += models.map(m => {
        const sel = m.id === current ? ' selected' : '';
        return `<option value="${escHtml(m.id)}"${sel}>${escHtml(m.label)}</option>`;
      }).join('');
    }
    select.innerHTML = opts;
  } catch (e) {
    let opts = '<option value="">(runtime default)</option>';
    if (current) opts += `<option value="${escHtml(current)}" selected>${escHtml(current)}</option>`;
    select.innerHTML = opts;
  }
}

async function populateFallbackRuntimeDropdown(selectEl, current = '') {
  let runtimes = ['copilot','claude','claude-sdk','gemini','opencode','wee'];
  try {
    const data = await apiRequest('GET', '/runtimes');
    const apiRuntimes = (data.runtimes || []).map(r => r.id).filter(Boolean);
    if (apiRuntimes.length) runtimes = apiRuntimes;
  } catch (e) {
    // Keep the conservative fallback list when the API is unavailable.
  }
  current = current || selectEl.value;
  selectEl.innerHTML = '<option value="">None (no fallback)</option>';
  runtimes.forEach(r => {
    const opt = document.createElement('option');
    opt.value = r; opt.textContent = r;
    if (r === current) opt.selected = true;
    selectEl.appendChild(opt);
  });
}

async function populateFallbackModelDropdown(selectEl, runtime = '', current = '') {
  const selectedRuntime = runtime || document.getElementById('sched-fallback-runtime')?.value || 'copilot';
  selectEl.innerHTML = '<option value="">None (no fallback)</option>';
  try {
    const data = await apiRequest('GET', `/models?runtime=${encodeURIComponent(selectedRuntime)}`);
    const models = data.models || [];
    models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.id; opt.textContent = m.label || m.id;
      if (m.id === current) opt.selected = true;
      selectEl.appendChild(opt);
    });
  } catch (e) {
    if (!current) return;
    const opt = document.createElement('option');
    opt.value = current; opt.textContent = current; opt.selected = true;
    selectEl.appendChild(opt);
  }
}

function wireJobForm(container, onSubmit) {
  // Populate fallback dropdowns (Issue #159)
  const fbRtEl = document.getElementById('sched-fallback-runtime');
  const fbModelEl = document.getElementById('sched-fallback-model');
  if (fbRtEl) populateFallbackRuntimeDropdown(fbRtEl);
  if (fbModelEl) populateFallbackModelDropdown(fbModelEl);
  if (fbRtEl && fbModelEl) {
    fbRtEl.addEventListener('change', () => populateFallbackModelDropdown(fbModelEl, fbRtEl.value));
  }

  const form = container.querySelector('#sched-job-form');
  const errEl = container.querySelector('#sched-form-error');
  const cancelBtn = container.querySelector('#btn-form-cancel');
  const aiFields = container.querySelector('#sched-ai-fields');
  const cmdFields = container.querySelector('#sched-cmd-fields');
  const execModeInput = form.querySelector('input[name="exec_mode"]');
  const taskLabel = container.querySelector('#sched-task-label');
  const taskInput = form.querySelector('textarea[name="task"]');
  const timeoutInput = form.querySelector('input[name="timeout"]');
  const timeoutDisplay = form.querySelector('#sched-timeout-display');

  // Live timeout display
  if (timeoutInput && timeoutDisplay) {
    timeoutInput.addEventListener('input', () => {
      const secs = parseInt(timeoutInput.value, 10);
      if (!isNaN(secs) && secs > 0) {
        timeoutDisplay.textContent = `= ${fmtTimeout(secs)}`;
      } else {
        timeoutDisplay.textContent = '';
      }
    });
  }

  // Wire schedule validation (AI-powered cron conversion preview)
  const schedInput = form.querySelector('#sched-schedule-input') || form.querySelector('input[name="schedule"]');
  const cronPreview = container.querySelector('#sched-cron-preview');
  let _schedValidateTimer = null;
  if (schedInput && cronPreview) {
    schedInput.addEventListener('input', () => {
      clearTimeout(_schedValidateTimer);
      const val = schedInput.value.trim();
      if (!val || val.length < 3) { cronPreview.classList.add('hidden'); return; }
      _schedValidateTimer = setTimeout(async () => {
        try {
          cronPreview.classList.remove('hidden');
          cronPreview.innerHTML = '<span class="cron-loading">\u23f3 Validating schedule\u2026</span>';
          const res = await schedulerRequest('POST', '/validate-schedule', { schedule: val });
          if (res.success && res.cron) {
            const methodBadge = res.method === 'ai' ? '\ud83e\udd16 AI' : res.method === 'deterministic' ? '\ud83d\udcd0 Parsed' : '\u2713';
            cronPreview.innerHTML = `<span class="cron-badge">${methodBadge}</span> <code>${escHtml(res.cron)}</code> \u2014 ${escHtml(res.human_readable)}${res.next_run ? ` <small>(next: ${fmtDate(res.next_run)})</small>` : ''}`;
            cronPreview.classList.remove('hidden', 'cron-error');
            cronPreview.classList.add('cron-ok');
          } else if (res.success && res.next_run) {
            cronPreview.innerHTML = `<span class="cron-badge">\u23f1 One-time</span> runs at <code>${escHtml(res.next_run)}</code>`;
            cronPreview.classList.remove('hidden', 'cron-error');
            cronPreview.classList.add('cron-ok');
          } else {
            cronPreview.innerHTML = '<span class="cron-badge cron-warn">\u26a0\ufe0f</span> Could not parse schedule \u2014 will try AI conversion on save';
            cronPreview.classList.remove('hidden', 'cron-ok');
            cronPreview.classList.add('cron-error');
          }
        } catch (e) {
          cronPreview.innerHTML = '<span class="cron-badge cron-warn">\u26a0\ufe0f</span> Validation unavailable';
          cronPreview.classList.remove('hidden', 'cron-ok');
          cronPreview.classList.add('cron-error');
        }
      }, 600);
    });
  }

  // Wire Task Type toggle buttons
  const toggleBtns = container.querySelectorAll('.mode-toggle-btn');
  toggleBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      toggleBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const isCmd = btn.dataset.mode === 'command';
      execModeInput.value = btn.dataset.mode;
      aiFields.classList.toggle('hidden', isCmd);
      cmdFields.classList.toggle('hidden', !isCmd);
      taskLabel.textContent = isCmd ? 'Command' : 'Task prompt';
      taskInput.placeholder = isCmd ? 'Shell command to execute…' : 'Describe the task the agent should perform…';
    });
  });

  if (cancelBtn) cancelBtn.addEventListener('click', closeSchedDetail);

  form.addEventListener('submit', async e => {
    e.preventDefault();
    errEl.classList.add('hidden');
    const data = Object.fromEntries(new FormData(form));
    if (!data.name?.trim())     { showFormErr(errEl, 'Name is required'); return; }
    if (!data.schedule?.trim()) { showFormErr(errEl, 'Schedule is required'); return; }
    if (!data.task?.trim())     { showFormErr(errEl, 'Task prompt is required'); return; }

    const timeoutSecs = parseInt(data.timeout, 10);
    if (isNaN(timeoutSecs) || timeoutSecs < 60) {
      showFormErr(errEl, 'Timeout must be at least 60 seconds'); return;
    }
    if (timeoutSecs > 3600) {
      showFormErr(errEl, 'Timeout cannot exceed 3600 seconds (1 hour) via the UI'); return;
    }

    const isCommand = data.exec_mode === 'command';
    const payload = {
      name:      data.name.trim(),
      schedule:  data.schedule.trim(),
      task:      data.task.trim(),
      recurring: !!data.recurring,
      notify:    !!data.notify,
      mode:      isCommand ? 'command' : (data.mode || 'restricted'),
      timeout:   timeoutSecs,
    };
    if (!isCommand) {
      payload.agent   = data.agent || 'orchestrator';
      payload.runtime = data.runtime || 'claude';
      payload.model   = data.model?.trim() || null;
      const fbRt = document.getElementById('sched-fallback-runtime')?.value || '';
      const fbModel = document.getElementById('sched-fallback-model')?.value || '';
      payload.fallback_runtime = fbRt;
      payload.fallback_model = fbModel;
    } else {
      payload.working_dir = data.working_dir?.trim() || '/opt';
    }

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

async function doJobRunNow(jobId, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Starting…'; }
  try {
    const result = await schedApi('POST', `/jobs/${jobId}/run`);
    const taskId = result.task_id || '';
    schedToast(taskId
      ? `✅ Job started — task ${taskId} running`
      : '✅ Job triggered successfully', 'success');
    await loadSchedulerJobs();
    const updated = SCHED.jobs.find(j => j.id === jobId);
    if (updated) renderJobDetailView(updated);
  } catch (err) {
    schedToast('Run failed: ' + err.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '▶ Run Now'; }
  }
}

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

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Background Tasks ───────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

async function bgApi(method, path, body = null) {
  return apiRequest(method, `/background-tasks${path}`, body);
}

const BG = {
  tasks: [],
  shownBgBanners: new Set(),
  selectedTaskId: null,
  isLoading: false,
  pollInterval: null,
  logsPoller: null,
  toolsPoller: null,
  activeDetailTab: null,
};

function startBgTaskPolling() {
  if (BG.pollInterval) return;
  updateBgBadge();
  BG.pollInterval = setInterval(async () => {
    try {
      const data = await bgApi('GET', '');
      BG.tasks = data.tasks || [];
      updateBgBadge();
      // If background panel is visible, refresh the list
      if (!$('background-panel').classList.contains('hidden')) {
        renderBgTasksSidebar();
        // If we have a selected task, do lightweight updates without destroying tab state
        if (BG.selectedTaskId) {
          const t = BG.tasks.find(x => x.task_id === BG.selectedTaskId);
          if (t) {
            if (t.status !== 'running' && t.status !== 'queued') {
              // Task finished — render detail once, then stop
              if (!BG.detailRenderedFinal) {
                BG.detailRenderedFinal = true;
                loadBgTaskDetail(BG.selectedTaskId);
              }
            } else {
              // Still running — just tick the elapsed timer; BG.logsPoller handles log updates
              const elapsedEl = document.querySelector('.bg-elapsed-live');
              if (elapsedEl) {
                const start = elapsedEl.dataset.start;
                if (start) elapsedEl.textContent = `Elapsed: ${formatElapsed(start)}`;
              }
            }
          }
        }
      }
    } catch { /* ignore polling errors */ }
  }, 5000);
}

function updateBgBadge() {
  const badge = $('bg-task-badge');
  if (!badge) return;
  const running = BG.tasks.filter(t => t.status === 'running').length;
  const queued = BG.tasks.filter(t => t.status === 'queued').length;
  const total = running + queued;
  if (total > 0) {
    badge.textContent = queued > 0 ? `${running}+${queued}` : running;
    show(badge);
  } else {
    hide(badge);
  }
}

async function loadBackgroundTasks(showToast = false) {
  if (BG.isLoading) return;
  BG.isLoading = true;
  const list = $('bg-sidebar-list');
  list.innerHTML = '<p class="bg-empty" style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px;">Loading…</p>';
  try {
    const data = await bgApi('GET', '');
    BG.tasks = data.tasks || [];
    renderBgTasksSidebar();
    updateBgBadge();
    if (showToast) bgToast('Tasks refreshed', 'success');
  } catch (err) {
    list.innerHTML = `<p class="bg-empty" style="padding:20px;text-align:center;color:#ff8888;font-size:13px;">Failed: ${escHtml(err.message)}</p>`;
  } finally {
    BG.isLoading = false;
  }
}

function renderBgTasksSidebar() {
  const list = $('bg-sidebar-list');
  if (!list) return;
  if (!BG.tasks.length) {
    list.innerHTML = '<p class="bg-empty" style="padding:24px 12px;text-align:center;color:var(--text-muted);font-size:13px;">No background tasks yet.<br>Use <code>/background &lt;prompt&gt;</code> in chat to start one.</p>';
    return;
  }

  // Sort: running first, then queued (by created_at asc = oldest first = pos 1), then completed/failed by created_at desc
  const statusOrder = { running: 0, queued: 1 };
  const sorted = [...BG.tasks].sort((a, b) => {
    const ao = statusOrder[a.status] ?? 2;
    const bo = statusOrder[b.status] ?? 2;
    if (ao !== bo) return ao - bo;
    if (a.status === 'queued') return (a.created_at || '').localeCompare(b.created_at || '');
    return (b.created_at || '').localeCompare(a.created_at || '');
  });

  const queuedTasks = sorted.filter(t => t.status === 'queued');
  const icons = { running: '🟢', completed: '✅', failed: '❌', killed: '🛑', queued: '⏳' };

  list.innerHTML = sorted.map(t => {
    const icon = icons[t.status] || '❓';
    const active = t.task_id === BG.selectedTaskId ? 'active' : '';
    const elapsed = t.status === 'running' ? ` · ${formatElapsed(t.created_at)}` : '';
    let statusLabel = t.status;
    if (t.status === 'queued') {
      const pos = queuedTasks.findIndex(q => q.task_id === t.task_id) + 1;
      statusLabel = `queued #${pos}`;
    }
    const prompt = escHtml((t.prompt || '').slice(0, 80));
    const agentLabel = escHtml(t.agent || '?');
    const dateStr = fmtDate(t.created_at);
    const fallbackBadge = t.used_fallback
      ? `<span class="bg-fallback-badge" title="Primary runtime failed; retried with ${escHtml(t.actual_runtime || '')}/${escHtml(t.actual_model || '')}">↩ Retried</span>`
      : '';
    return `
      <div class="session-item bg-sidebar-item ${active}" onclick="selectBgTask('${t.task_id}')">
        <div class="session-title"><span class="bg-task-title-text">${icon} ${prompt || '(no prompt)'}</span>${fallbackBadge}</div>
        <div class="session-preview">${agentLabel} · ${statusLabel}${elapsed} · ${dateStr}</div>
      </div>`;
  }).join('');
}

// Keep backward compat alias
function renderBgTasks() { renderBgTasksSidebar(); }

function formatElapsed(isoDate) {
  if (!isoDate) return '';
  try {
    const withTz = /(?:Z|[+-]\d{2}:\d{2})$/.test(isoDate) ? isoDate : `${isoDate}Z`;
    const start = new Date(withTz);
    const startMs = start.getTime();
    if (Number.isNaN(startMs)) return '';
    const secs = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    const remSecs = secs % 60;
    return `${mins}m ${remSecs}s`;
  } catch { return ''; }
}

// Make selectBgTask global so onclick works
window.selectBgTask = function(taskId) {
  BG.selectedTaskId = taskId;
  BG.activeDetailTab = null;  // reset tab choice for new task
  BG.detailRenderedFinal = false;
  renderBgTasks();
  loadBgTaskDetail(taskId);
  // On mobile, close sidebar to show detail panel
  if (isMobileViewport()) toggleSidebar(false);
};

async function loadBgTaskDetail(taskId) {
  const detail = $('bg-detail');
  const body = $('bg-detail-body');

  // Clear any previous logs poller
  if (BG.logsPoller) {
    clearInterval(BG.logsPoller);
    BG.logsPoller = null;
  }

  try {
    const t = await bgApi('GET', `/${taskId}`);
    const icons = { running: '🟢', completed: '✅', failed: '❌', killed: '🛑', queued: '⏳' };
    const icon = icons[t.status] || '❓';
    const statusClass = `bg-status-${t.status}`;
    const elapsed = t.status === 'running' ? formatElapsed(t.created_at) : '';

    let statusLabel = t.status;
    if (t.status === 'queued') {
      const queuedInList = BG.tasks.filter(x => x.status === 'queued').sort((a,b) => (a.created_at||'').localeCompare(b.created_at||''));
      const pos = queuedInList.findIndex(x => x.task_id === t.task_id) + 1;
      if (pos > 0) statusLabel = `queued #${pos}`;
    }

    let actionsHtml = '';
    if (t.status === 'running') {
      actionsHtml = `<div class="bg-detail-actions">
        <button class="btn btn-ghost btn-sm" onclick="showBgTaskLogs('${t.task_id}')">📋 Live Logs</button>
        <button class="btn btn-danger btn-sm" onclick="killBgTask('${t.task_id}')">🛑 Kill Task</button>
      </div>`;
    } else if (t.status === 'queued') {
      actionsHtml = `<div class="bg-detail-actions">
        <button class="btn btn-danger btn-sm" onclick="killBgTask('${t.task_id}')">✖ Cancel</button>
      </div>`;
    } else {
      actionsHtml = `<div class="bg-detail-actions">
        <button class="btn btn-ghost btn-sm" onclick="showBgTaskLogs('${t.task_id}')">📋 Logs</button>
        <button class="btn btn-ghost btn-sm" onclick="viewBgTranscript('${t.task_id}')">📄 Transcript</button>
        <button class="btn btn-ghost btn-sm" onclick="deleteBgTask('${t.task_id}')">🗑️ Remove</button>
      </div>`;
    }

    const timingHtml = (() => {
      const started = t.created_at ? fmtDate(t.created_at) : '—';
      const finished = t.completed_at ? fmtDate(t.completed_at) : null;
      const dur = (t.created_at && t.completed_at)
        ? (() => {
            try {
              const s = new Date(t.created_at).getTime();
              const e = new Date(t.completed_at).getTime();
              const sec = Math.round((e - s) / 1000);
              return sec < 60 ? `${sec}s` : `${Math.floor(sec/60)}m ${sec%60}s`;
            } catch { return null; }
          })()
        : null;
      return `<div class="bg-transcript-timing">
        <span>⏱ Started: ${escHtml(started)}</span>
        ${elapsed ? `<span class="bg-elapsed-live" data-start="${escHtml(t.created_at || '')}">Elapsed: ${elapsed}</span>` : ''}
        ${finished ? `<span>Finished: ${escHtml(finished)}</span>` : ''}
        ${dur ? `<span>Duration: ${dur}</span>` : ''}
      </div>`;
    })();

    const toolCallBadge = t.tool_call_count ? `<span class="bg-tool-badge">${t.tool_call_count}</span>` : '';

    body.innerHTML = `
      <div class="bg-detail-split">
        <div class="bg-detail-left">
          <div class="bg-detail-tabs">
            <button class="bg-tab active" data-tab="details">Details</button>
            <button class="bg-tab" data-tab="tools">🔧 Tools ${toolCallBadge}${t.status === 'running' ? '<span class="bg-live-dot"></span>' : ''}</button>
          </div>
          <div id="bg-tab-details" class="bg-tab-pane">
            <div class="bg-detail-meta">
              <div class="bg-detail-meta-row">
                <span class="bg-detail-meta-label">Task ID</span>
                <span class="bg-detail-meta-value" style="font-family:var(--font-mono);font-size:11px">${escHtml(taskId)}</span>
              </div>
              <div class="bg-detail-meta-row">
                <span class="bg-detail-meta-label">Status</span>
                <span class="bg-card-status ${statusClass}">${icon} ${statusLabel}</span>
                ${elapsed ? `<span style="font-size:11px;color:var(--text-muted);margin-left:8px">${elapsed}</span>` : ''}
                ${t.status === 'queued' ? `<span style="font-size:11px;color:var(--text-muted);margin-left:8px">Waiting for a slot to open…</span>` : ''}
              </div>
              <div class="bg-detail-meta-row">
                <span class="bg-detail-meta-label">Agent</span>
                <span class="bg-detail-meta-value">${escHtml(t.agent || '?')}</span>
              </div>
              <div class="bg-detail-meta-row">
                <span class="bg-detail-meta-label">Runtime</span>
                <span class="bg-detail-meta-value">${runtimeIconHTML(t.runtime)}${escHtml(t.runtime || '?')} / ${escHtml(t.model || '?')}</span>
              </div>
              ${t.used_fallback ? `<div class="bg-detail-meta-row">
                <span class="bg-detail-meta-label">Fallback</span>
                <span class="bg-detail-meta-value bg-fallback-detail" title="Primary runtime failed; task completed on fallback runtime">
                  <span class="bg-fallback-badge">↩ Retried</span>
                  <span class="bg-fallback-route">${runtimeIconHTML(t.runtime)}${escHtml(t.runtime || '?')} / ${escHtml(t.model || '?')} → ${runtimeIconHTML(t.actual_runtime || '')}${escHtml(t.actual_runtime || '?')} / ${escHtml(t.actual_model || '?')}</span>
                  <span class="bg-fallback-tip">Primary failed; completed on fallback runtime</span>
                </span>
              </div>` : ''}
              <div class="bg-detail-meta-row">
                <span class="bg-detail-meta-label">Started</span>
                <span class="bg-detail-meta-value">${fmtDate(t.created_at)}</span>
              </div>
              ${t.completed_at ? `<div class="bg-detail-meta-row">
                <span class="bg-detail-meta-label">Finished</span>
                <span class="bg-detail-meta-value">${fmtDate(t.completed_at)}</span>
              </div>` : ''}
            </div>
            <div class="bg-detail-prompt">${escHtml(t.prompt || '')}</div>
            ${actionsHtml}
          </div>
          <div id="bg-tab-tools" class="bg-tab-pane hidden">
            <div class="bg-tool-calls-panel" id="bg-tools-${taskId}">
              <p style="color:var(--text-muted);font-size:12px;padding:8px">Loading tool calls…</p>
            </div>
          </div>
        </div>
        <div class="bg-detail-right">
          <div class="bg-logs-header">
            <span class="bg-logs-title">Logs ${t.status === 'running' ? '<span class="bg-live-dot"></span>' : ''}</span>
          </div>
          ${timingHtml}
          <div class="bg-transcript-panel" id="bg-transcript-${taskId}">
            <p style="color:var(--text-muted);font-size:12px;padding:8px">Loading logs…</p>
          </div>
        </div>
      </div>
    `;

    // Tab switching (Details / Tools only — Logs always visible on right)
    body.querySelectorAll('.bg-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        body.querySelectorAll('.bg-tab').forEach(t2 => t2.classList.remove('active'));
        tab.classList.add('active');
        body.querySelectorAll('.bg-detail-left .bg-tab-pane').forEach(p => hide(p));
        const pane = $(`bg-tab-${tab.dataset.tab}`);
        show(pane);
        BG.activeDetailTab = tab.dataset.tab;
        if (tab.dataset.tab === 'tools') loadBgTaskToolCalls(taskId, t.status);
      });
    });

    // Always load logs immediately (visible on right side)
    loadBgTaskLogs(taskId, t.status);

    // Restore previously active left tab, or default to details
    const tabToOpen = BG.activeDetailTab || 'details';
    if (tabToOpen !== 'logs') {
      const targetTab = body.querySelector(`.bg-tab[data-tab="${tabToOpen}"]`);
      if (targetTab) targetTab.click();
    }

  } catch (err) {
    body.innerHTML = `<p style="color:#ff8888">Failed to load: ${escHtml(err.message)}</p>`;
  }
}
window.loadBgTaskDetail = loadBgTaskDetail;

async function loadBgTaskLogs(taskId, status) {
  const panel = $(`bg-transcript-${taskId}`);
  if (!panel) return;

  // If already loaded for a finished task, skip re-fetch (prevents flicker)
  const currentStatus = BG.tasks.find(x => x.task_id === taskId)?.status || status;
  if (panel.dataset.loaded === 'true' && currentStatus !== 'running' && currentStatus !== 'queued') return;

  // Clear any previous poller
  if (BG.logsPoller) {
    clearInterval(BG.logsPoller);
    BG.logsPoller = null;
  }

  async function fetchAndRender() {
    try {
      const currentStatus = BG.tasks.find(x => x.task_id === taskId)?.status || status;
      let lines = [];
      let error = null;
      let finalResponse = null;

      if (currentStatus === 'running' || currentStatus === 'queued') {
        const t = await bgApi('GET', `/${taskId}`);
        lines = t.recent_output || [];
        error = t.error;
      } else {
        const data = await bgApi('GET', `/${taskId}/transcript`);
        if (data.final_response) {
          finalResponse = data.final_response;
          lines = data.final_response.split('\n');
        } else {
          lines = data.output_lines || [];
          error = data.error;
        }
      }

      if (!lines.length && !error && !finalResponse) {
        panel.innerHTML = '<p style="color:var(--text-muted);font-size:12px;padding:8px">No output yet…</p>';
        return;
      }

      const wasAtBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 40;
      const isRunning = currentStatus === 'running';

      // For completed tasks with final_response, render as markdown
      if (finalResponse && typeof marked !== 'undefined' && !isRunning) {
        try {
          const cleanResponse = finalResponse.split('\n').filter(l => !isInternalMarkerLine(l)).join('\n');
          const html = marked.parse(cleanResponse, { breaks: true, gfm: true });
          const sanitized = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html) : html;
          panel.innerHTML = `<div class="bg-log-rendered">${sanitized}</div>`;
        } catch {
          panel.innerHTML = `<pre class="bg-transcript-pre">${escHtml(finalResponse)}</pre>`;
        }
      } else {
        // Pre-formatted lines (running tasks or fallback)
        const renderedLines = lines.filter(l => !isInternalMarkerLine(l)).map((line, i) => {
          const esc = escHtml(line);
          if (!detectToolCallLine(line)) return esc;
          const isLastLine = i === lines.length - 1 && isRunning;
          const gearCls = isLastLine ? 'tool-gear bg-tool-spinning' : 'tool-gear';
          return '<span class="bg-tool-line"><span class="' + gearCls + '">⚙️</span> ' + esc + '</span>';
        }).join('\n');
        panel.innerHTML = `<pre class="bg-transcript-pre">${renderedLines}${error ? `\n\n[error] ${error}` : ''}</pre>`;
      }

      if (wasAtBottom || isRunning) {
        panel.scrollTop = panel.scrollHeight;
      }

      // Mark as loaded for finished tasks to prevent re-fetching
      if (currentStatus !== 'running' && currentStatus !== 'queued') {
        panel.dataset.loaded = 'true';
      }
    } catch { /* ignore transient fetch errors */ }
  }

  await fetchAndRender();

  // Poll every 3s for live output while task is running
  const liveStatus = BG.tasks.find(x => x.task_id === taskId)?.status || status;
  if (liveStatus === 'running') {
    BG.logsPoller = setInterval(async () => {
      const current = BG.tasks.find(x => x.task_id === taskId)?.status;
      await fetchAndRender();
      const elapsedEl = document.querySelector('.bg-elapsed-live');
      if (elapsedEl) {
        const start = elapsedEl.dataset.start;
        if (start) elapsedEl.textContent = `Elapsed: ${formatElapsed(start)}`;
      }
      if (current && current !== 'running' && current !== 'queued') {
        clearInterval(BG.logsPoller);
        BG.logsPoller = null;
      }
    }, 3000);
  }
}

// ── Tool Call Panel ─────────────────────────────────────────

async function loadBgTaskToolCalls(taskId, status) {
  const panel = $(`bg-tools-${taskId}`);
  if (!panel) return;

  // Clear any previous tool poller
  if (BG.toolsPoller) {
    clearInterval(BG.toolsPoller);
    BG.toolsPoller = null;
  }

  async function fetchAndRenderTools() {
    try {
      const data = await bgApi('GET', `/${taskId}/tool-calls`);
      const calls = data.tool_calls || [];

      if (!calls.length) {
        panel.innerHTML = `<div class="bg-tools-empty">
          <span style="font-size:24px">🔧</span>
          <p>No tool calls detected yet.</p>
          <p style="font-size:11px;color:var(--text-muted)">Tool calls will appear here as the agent works.<br>Best visibility with Claude runtime (stream-json).</p>
        </div>`;
        return;
      }

      const wasAtBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 40;

      panel.innerHTML = `
        <div class="bg-tools-header">
          <span class="bg-tools-count">${calls.length} tool call${calls.length !== 1 ? 's' : ''}</span>
          <span class="bg-tools-runtime">${escHtml(data.status || '')}</span>
        </div>
        <div class="bg-tools-timeline">
          ${calls.map((tc, i) => renderToolCall(tc, i)).join('')}
        </div>
      `;

      // Toggle expand/collapse on tool call items
      panel.querySelectorAll('.bg-tc-header').forEach(hdr => {
        hdr.addEventListener('click', () => {
          const item = hdr.closest('.bg-tc-item');
          item.classList.toggle('expanded');
        });
      });

      if (wasAtBottom || status === 'running') {
        panel.scrollTop = panel.scrollHeight;
      }
    } catch (err) {
      panel.innerHTML = `<p style="color:#ff8888;font-size:12px;padding:8px">Error loading tool calls: ${escHtml(err.message)}</p>`;
    }
  }

  await fetchAndRenderTools();

  // Poll for live tool calls while task is running
  const liveStatus = BG.tasks.find(x => x.task_id === taskId)?.status || status;
  if (liveStatus === 'running') {
    BG.toolsPoller = setInterval(async () => {
      const current = BG.tasks.find(x => x.task_id === taskId)?.status;
      await fetchAndRenderTools();
      if (current && current !== 'running' && current !== 'queued') {
        clearInterval(BG.toolsPoller);
        BG.toolsPoller = null;
      }
    }, 3000);
  }
}

function renderToolCall(tc, index) {
  const statusIcons = {
    'detected': '🔵',
    'start': '🟡',
    'input_complete': '🟠',
    'result': tc.is_error ? '🔴' : '🟢',
    'completed': '🟢',
    'failed': '🔴',
  };
  const statusColors = {
    'detected': 'var(--tc-detected, #5599ff)',
    'start': 'var(--tc-pending, #ffaa33)',
    'input_complete': 'var(--tc-running, #ff8833)',
    'result': tc.is_error ? 'var(--tc-failed, #ff4444)' : 'var(--tc-success, #44cc66)',
    'completed': 'var(--tc-success, #44cc66)',
    'failed': 'var(--tc-failed, #ff4444)',
  };
  const icon = statusIcons[tc.event || tc.status] || '⚪';
  const color = statusColors[tc.event || tc.status] || '#888';
  const name = escHtml(tc.name || 'unknown');
  const tcId = escHtml(tc.id || '');
  const timestamp = tc.timestamp || tc.started_at || '';
  const timeStr = timestamp ? fmtTime(timestamp) : '';

  let inputHtml = '';
  if (tc.input !== undefined && tc.input !== null && tc.input !== '') {
    const inputStr = typeof tc.input === 'object' ? JSON.stringify(tc.input, null, 2) : String(tc.input);
    if (inputStr.length > 0) {
      inputHtml = `<div class="bg-tc-section">
        <div class="bg-tc-section-label">Input</div>
        <pre class="bg-tc-code">${escHtml(inputStr)}</pre>
      </div>`;
    }
  }

  let outputHtml = '';
  if (tc.output !== undefined && tc.output !== null && tc.output !== '') {
    const outputStr = typeof tc.output === 'object' ? JSON.stringify(tc.output, null, 2) : String(tc.output);
    outputHtml = `<div class="bg-tc-section">
      <div class="bg-tc-section-label">Output</div>
      <pre class="bg-tc-code">${escHtml(outputStr.substring(0, 2000))}${outputStr.length > 2000 ? '\n…truncated' : ''}</pre>
    </div>`;
  }

  const runtimeBadge = tc.runtime ? `<span class="bg-tc-runtime">${escHtml(tc.runtime)}</span>` : '';

  return `
    <div class="bg-tc-item" style="--tc-color: ${color}">
      <div class="bg-tc-connector"></div>
      <div class="bg-tc-header">
        <span class="bg-tc-icon">${icon}</span>
        <span class="bg-tc-name">${name}</span>
        ${runtimeBadge}
        <span class="bg-tc-time">${timeStr}</span>
        <span class="bg-tc-expand">▶</span>
      </div>
      <div class="bg-tc-body">
        <div class="bg-tc-id">${tcId}</div>
        ${inputHtml}
        ${outputHtml}
      </div>
    </div>
  `;
}

function fmtTime(isoDate) {
  if (!isoDate) return '';
  try {
    const withTz = /(?:Z|[+-]\d{2}:\d{2})$/.test(isoDate) ? isoDate : `${isoDate}Z`;
    const d = new Date(withTz);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch { return ''; }
}

function closeBgDetail() {
  if (BG.logsPoller) {
    clearInterval(BG.logsPoller);
    BG.logsPoller = null;
  }
  if (BG.toolsPoller) {
    clearInterval(BG.toolsPoller);
    BG.toolsPoller = null;
  }
  $('bg-detail-body').innerHTML = '<p class="bg-detail-empty">Select a task from the sidebar to view details</p>';
  BG.selectedTaskId = null;
  BG.activeDetailTab = null;
  renderBgTasks();
}

window.killBgTask = async function(taskId) {
  try {
    await bgApi('DELETE', `/${taskId}`);
    bgToast('Task killed', 'success');
    await loadBackgroundTasks();
    closeBgDetail();
  } catch (err) {
    bgToast(`Kill failed: ${err.message}`, 'error');
  }
};

window.deleteBgTask = async function(taskId) {
  try {
    await bgApi('DELETE', `/${taskId}`);
    bgToast('Task removed', 'success');
    await loadBackgroundTasks();
    closeBgDetail();
  } catch (err) {
    bgToast(`Remove failed: ${err.message}`, 'error');
  }
};

window.viewBgTranscript = async function(taskId) {
  const body = $('bg-detail-body');
  try {
    const data = await bgApi('GET', `/${taskId}/transcript`);
    let content = '';
    if (data.final_response) {
      content = data.final_response;
    } else if (data.output_lines && data.output_lines.length) {
      content = data.output_lines.join('\n');
    } else if (data.error) {
      content = `Error: ${data.error}`;
    } else {
      content = '(no output)';
    }

    // Try to render as markdown if marked is available
    let renderedHtml;
    if (typeof marked !== 'undefined' && data.final_response) {
      try {
        renderedHtml = marked.parse(content);
      } catch {
        renderedHtml = `<pre>${escHtml(content)}</pre>`;
      }
    } else {
      renderedHtml = `<pre>${escHtml(content)}</pre>`;
    }

    body.innerHTML = `
      <div style="margin-bottom:12px">
        <button class="btn btn-ghost btn-sm" onclick="loadBgTaskDetail('${taskId}')">← Back to Detail</button>
      </div>
      <div class="bg-detail-output" style="max-height:none">${renderedHtml}</div>
    `;
  } catch (err) {
    body.innerHTML = `<p style="color:#ff8888">Failed: ${escHtml(err.message)}</p>`;
  }
};

// Live Logs viewer
let _bgLogsPollerTimer = null;

window.showBgTaskLogs = async function(taskId) {
  const body = $('bg-detail-body');
  if (_bgLogsPollerTimer) { clearInterval(_bgLogsPollerTimer); _bgLogsPollerTimer = null; }

  async function renderLogs() {
    try {
      const data = await bgApi('GET', '/' + taskId + '/logs');
      const lines = data.output_lines || [];
      const status = data.status || 'unknown';
      const started = data.created_at ? fmtDate(data.created_at) : '--';
      const finished = data.completed_at ? fmtDate(data.completed_at) : '';
      const elapsed = data.created_at ? formatElapsed(data.created_at) : '';
      const sicons = { running: '\u{1F7E2}', completed: '\u2705', failed: '\u274C', queued: '\u23F3' };
      const icon = sicons[status] || '\u2753';
      let timing = '<div style="font-size:11px;color:var(--text-muted);margin-bottom:8px">' + icon + ' ' + escHtml(status);
      if (started) timing += ' &middot; Started: ' + escHtml(started);
      if (finished) timing += ' &middot; Finished: ' + escHtml(finished);
      else if (elapsed) timing += ' &middot; Elapsed: ' + escHtml(elapsed);
      timing += '</div>';
      const logText = lines.length ? lines.join('\n') : '(no output yet)';
      const oldDiv = $('bg-logs-output');
      const atBottom = !oldDiv || (oldDiv.scrollHeight - oldDiv.scrollTop <= oldDiv.clientHeight + 40);
      const polling = status === 'running' ? '<span style="font-size:11px;color:var(--text-muted)">Auto-updating...</span>' : '';
      body.innerHTML = '<div style="margin-bottom:12px;display:flex;gap:8px;align-items:center"><button class="btn btn-ghost btn-sm" onclick="loadBgTaskDetail(\'' + taskId + '\')">&#8592; Back</button>' + polling + '</div>'
        + timing + '<pre id="bg-logs-output" style="background:var(--bg-secondary,#111);border:1px solid var(--border-color,#333);border-radius:6px;padding:12px;font-size:11px;line-height:1.5;max-height:420px;overflow-y:auto;white-space:pre-wrap;word-break:break-all">' + escHtml(logText) + '</pre>';
      const nd = $('bg-logs-output');
      if (nd && atBottom) nd.scrollTop = nd.scrollHeight;
      if (status !== 'running' && _bgLogsPollerTimer) { clearInterval(_bgLogsPollerTimer); _bgLogsPollerTimer = null; }
    } catch (err) {
      body.innerHTML = '<div style="margin-bottom:12px"><button class="btn btn-ghost btn-sm" onclick="loadBgTaskDetail(\'' + taskId + '\')">&#8592; Back</button></div><p style="color:#ff8888">Log error: ' + escHtml(err.message) + '</p>';
      if (_bgLogsPollerTimer) { clearInterval(_bgLogsPollerTimer); _bgLogsPollerTimer = null; }
    }
  }

  body.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">Loading logs...</p>';
  await renderLogs();
  try {
    const s = await bgApi('GET', '/' + taskId + '/logs');
    if (s.status === 'running') _bgLogsPollerTimer = setInterval(renderLogs, 2000);
  } catch (_) {}
};

// Toast helper
let _bgToastTimer = null;
function bgToast(msg, type = 'info') {
  let toast = $('bg-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'bg-toast';
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.className = `sched-toast sched-toast-${type}`;
  toast.style.opacity = '1';
  clearTimeout(_bgToastTimer);
  _bgToastTimer = setTimeout(() => { toast.style.opacity = '0'; }, 2500);
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Notification Center ────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

const NOTIF = {
  notifications: [],
  pollInterval: null,
  // IDs of notifications shown as popups so we don't show them twice
  shownPopups: new Set(JSON.parse(localStorage.getItem('wee_shown_notif_popups') || '[]')),
};

function isNotificationsEnabled() {
  return localStorage.getItem('wee_notifications_enabled') !== 'false';
}

function setNotificationsEnabled(val) {
  localStorage.setItem('wee_notifications_enabled', val ? 'true' : 'false');
  // Sync to backend global toggle
  apiRequest('PUT', '/settings/notifications', { notifications_enabled: !!val })
    .catch(() => { /* best-effort sync */ });
}

/** Fetch the global notification toggle from the backend and sync localStorage. */
async function syncNotificationToggleFromBackend() {
  try {
    const data = await apiRequest('GET', '/settings/notifications');
    if (data && typeof data.notifications_enabled === 'boolean') {
      localStorage.setItem('wee_notifications_enabled', data.notifications_enabled ? 'true' : 'false');
      const toggle = $('notif-enabled-toggle');
      if (toggle) toggle.checked = data.notifications_enabled;
    }
  } catch { /* backend unavailable — keep localStorage value */ }
}

async function fetchNotifications() {
  try {
    const data = await apiRequest('GET', '/notifications');
    return data;
  } catch { return null; }
}

function startNotificationPolling() {
  if (NOTIF.pollInterval) return;
  // Initial fetch
  pollNotifications();
  NOTIF.pollInterval = setInterval(pollNotifications, 10000);
}

async function pollNotifications() {
  if (!isNotificationsEnabled()) return;
  const data = await fetchNotifications();
  if (!data) return;

  const prev = NOTIF.notifications;
  NOTIF.notifications = data.notifications || [];

  // Show popup for newly completed/failed tasks (not previously shown)
  let hasNew = false;
  const prevIds = new Set(prev.map(n => n.notification_id));
  for (const n of NOTIF.notifications) {
    if (!prevIds.has(n.notification_id) && !NOTIF.shownPopups.has(n.notification_id)) {
      showNotificationPopup(n);
      NOTIF.shownPopups.add(n.notification_id);
      hasNew = true;
    }
  }
  // Persist shown popup IDs (keep last 100)
  const arr = Array.from(NOTIF.shownPopups).slice(-100);
  localStorage.setItem('wee_shown_notif_popups', JSON.stringify(arr));

  updateNotifBadge(data.unread_count || 0);

  // Auto-open notification panel when new notifications arrive (on chat view)
  if (hasNew && $('chat-panel') && !$('chat-panel').classList.contains('hidden')) {
    $('notification-panel').classList.remove('notif-hidden');
    $('btn-nav-notifications').classList.add('active');
  }

  // If notification panel is visible, re-render
  if (!$('notification-panel').classList.contains('notif-hidden')) {
    renderNotifications();
  }
}

function updateNotifBadge(count) {
  const badge = $('notif-badge');
  if (!badge) return;
  if (count > 0) {
    badge.textContent = count;
    show(badge);
  } else {
    hide(badge);
  }
}

function toggleNotificationPanel() {
  const panel = $('notification-panel');
  const isHidden = panel.classList.contains('notif-hidden');
  if (isHidden) {
    panel.classList.remove('notif-hidden');
    $('btn-nav-notifications').classList.add('active');
    renderNotifications();
  } else {
    panel.classList.add('notif-hidden');
    $('btn-nav-notifications').classList.remove('active');
  }
}

function hideNotificationPanel() {
  $('notification-panel').classList.add('notif-hidden');
  $('btn-nav-notifications').classList.remove('active');
}

function renderNotifications() {
  const list = $('notif-list');
  if (!list) return;

  if (!NOTIF.notifications.length) {
    list.innerHTML = '<p class="notif-empty">No notifications yet.<br>Background task completions will appear here.</p>';
    return;
  }

  list.innerHTML = NOTIF.notifications.map(n => {
    const isSuccess = n.status === 'completed';
    const icon = isSuccess ? '✓' : '✗';
    const titleText = isSuccess ? 'Task completed' : 'Task failed';
    const cardClass = `notif-card ${isSuccess ? 'notif-success' : 'notif-failure'} ${n.read ? 'notif-read' : 'notif-unread'}`;
    const titleClass = isSuccess ? 'notif-title-success' : 'notif-title-failure';

    let previewHtml = '';
    if (isSuccess && n.output_preview) {
      previewHtml = `<div class="notif-preview">${escHtml(n.output_preview.slice(0, 300))}</div>`;
    } else if (!isSuccess && n.error) {
      previewHtml = `<div class="notif-error-preview">${escHtml(n.error.slice(0, 300))}</div>`;
    }

    return `
      <div class="${cardClass}" data-notif-id="${n.notification_id}">
        <div class="notif-card-header">
          <div class="notif-card-icon-title">
            <span class="notif-card-icon">${icon}</span>
            <span class="notif-card-title ${titleClass}">${escHtml(titleText)}</span>
          </div>
          <div class="notif-card-actions">
            ${!n.read ? `<button class="notif-card-btn" onclick="notifMarkRead('${n.notification_id}')">✓ Read</button>` : ''}
            <button class="notif-card-btn notif-btn-danger" onclick="notifDelete('${n.notification_id}')">✕</button>
          </div>
        </div>
        <div class="notif-card-description">${escHtml(n.description || '')}</div>
        ${previewHtml}
        <div class="notif-card-meta">
          <span class="notif-card-task-id">${escHtml(n.task_id || '')}</span>
          <span class="notif-card-time">${fmtDate(n.created_at)}</span>
        </div>
      </div>`;
  }).join('');
}

window.notifMarkRead = async function(notifId) {
  try {
    await apiRequest('POST', `/notifications/${notifId}/read`);
    await pollNotifications();
  } catch (e) { /* ignore */ }
};

window.notifDelete = async function(notifId) {
  try {
    await apiRequest('DELETE', `/notifications/${notifId}`);
    NOTIF.notifications = NOTIF.notifications.filter(n => n.notification_id !== notifId);
    updateNotifBadge(NOTIF.notifications.filter(n => !n.read).length);
    renderNotifications();
  } catch (e) { /* ignore */ }
};

// Floating popup notification (bottom-right corner)
let _notifPopupContainer = null;
function getNotifPopupContainer() {
  if (!_notifPopupContainer) {
    _notifPopupContainer = document.createElement('div');
    _notifPopupContainer.id = 'notif-popup-container';
    document.body.appendChild(_notifPopupContainer);
  }
  return _notifPopupContainer;
}

function showNotificationPopup(notification) {
  if (!isNotificationsEnabled()) return;
  const isSuccess = notification.status === 'completed';
  const icon = isSuccess ? '✅' : '❌';
  const title = isSuccess ? '✓ Task completed' : '✗ Task failed';
  const desc = (notification.description || '').slice(0, 80);
  const popupClass = isSuccess ? 'notif-popup-success' : 'notif-popup-failure';
  const titleClass = isSuccess ? 'notif-popup-title-success' : 'notif-popup-title-failure';

  const popup = document.createElement('div');
  popup.className = `notif-popup ${popupClass}`;
  popup.innerHTML = `
    <span class="notif-popup-icon">${icon}</span>
    <div class="notif-popup-body">
      <div class="notif-popup-title ${titleClass}">${escHtml(title)}</div>
      <div class="notif-popup-desc">${escHtml(desc)}</div>
    </div>
    <button class="notif-popup-close" title="Dismiss">✕</button>
  `;

  const container = getNotifPopupContainer();
  container.appendChild(popup);

  popup.querySelector('.notif-popup-close').addEventListener('click', () => {
    popup.style.opacity = '0';
    popup.style.transform = 'translateX(20px)';
    popup.style.transition = 'opacity 0.2s, transform 0.2s';
    setTimeout(() => popup.remove(), 220);
  });

  // Auto-dismiss after 7 seconds
  setTimeout(() => {
    if (popup.parentNode) {
      popup.style.opacity = '0';
      popup.style.transform = 'translateX(20px)';
      popup.style.transition = 'opacity 0.3s, transform 0.3s';
      setTimeout(() => popup.remove(), 320);
    }
  }, 7000);
}

// ─── Microphone Recording ────────────────────────────────────────────────────

const MIC = {
  mediaRecorder: null,
  audioChunks: [],
  isRecording: false,
  startTime: 0,
  timerInterval: null,
  stream: null,
  canRecord: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
};

function _initMic() {
  const btn = $('btn-mic');
  if (!btn) return;
  if (btn._micBound) return;
  btn._micBound = true;

  // Create hidden audio file input for fallback upload
  let audioInput = $('audio-file-input');
  if (!audioInput) {
    audioInput = document.createElement('input');
    audioInput.type = 'file';
    audioInput.id = 'audio-file-input';
    audioInput.accept = 'audio/*,.ogg,.webm,.mp3,.wav,.m4a,.flac,.aac';
    audioInput.className = 'hidden';
    audioInput.style.display = 'none';
    btn.parentElement.appendChild(audioInput);
    audioInput.addEventListener('change', handleAudioFileUpload);
  }

  if (!MIC.canRecord) {
    // Non-secure context: mic button becomes audio upload button
    btn.title = 'Upload audio file for transcription (live recording requires HTTPS)';
  }

  btn.addEventListener('click', () => {
    if (MIC.canRecord) {
      if (MIC.isRecording) stopRecording();
      else startRecording();
    } else {
      // Fallback: open file picker for audio
      if (!STATE.currentSessionId) {
        renderSystemMessage('⚠️ Start or select a session first.');
        return;
      }
      if (!MIC._warnedOnce) {
        MIC._warnedOnce = true;
        renderSystemMessage('🔒 Live mic recording requires HTTPS. Select an audio file to transcribe instead.');
      }
      $('audio-file-input').click();
    }
  });
}

async function handleAudioFileUpload(e) {
  const file = e.target.files?.[0];
  e.target.value = ''; // reset for next pick
  if (!file) return;
  if (file.size > 25 * 1024 * 1024) {
    renderSystemMessage('⚠️ Audio file exceeds 25 MB limit.');
    return;
  }
  const btn = $('btn-mic');
  btn.classList.add('transcribing');
  btn.title = 'Transcribing…';
  renderSystemMessage(`🎤 Uploading ${file.name} (${formatFileSize(file.size)})…`);

  try {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${API_BASE}/sessions/${STATE.currentSessionId}/transcribe`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${STATE.token}` },
      body: form,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    if (data.text) {
      const textarea = $('message-input');
      const existing = textarea.value;
      textarea.value = existing ? (existing + ' ' + data.text) : data.text;
      autoResizeTextarea(textarea);
      syncMirror();
      $('btn-send').disabled = false;
      textarea.focus();
      renderSystemMessage(`🎤 Transcribed via ${data.backend} (${formatFileSize(data.size)})`);
    } else {
      renderSystemMessage('⚠️ No speech detected in audio file.');
    }
  } catch (err) {
    renderSystemMessage('❌ Transcription failed: ' + err.message);
  } finally {
    btn.classList.remove('transcribing');
    btn.title = MIC.canRecord ? 'Click to record voice' : 'Upload audio file for transcription';
  }
}

async function startRecording() {
  const btn = $('btn-mic');
  if (!STATE.currentSessionId) {
    renderSystemMessage('⚠️ Start or select a session first.');
    return;
  }
  try {
    MIC.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    renderSystemMessage('⚠️ Microphone access denied: ' + err.message);
    return;
  }

  MIC.audioChunks = [];
  // Prefer webm/opus, fall back to whatever is available
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
  MIC.mediaRecorder = new MediaRecorder(MIC.stream, mimeType ? { mimeType } : {});

  MIC.mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) MIC.audioChunks.push(e.data);
  };
  MIC.mediaRecorder.onstop = () => {
    const blob = new Blob(MIC.audioChunks, { type: MIC.mediaRecorder.mimeType || 'audio/webm' });
    MIC.stream.getTracks().forEach(t => t.stop());
    MIC.stream = null;
    transcribeAndInsert(blob);
  };

  MIC.mediaRecorder.start(250); // collect data every 250ms
  MIC.isRecording = true;
  MIC.startTime = Date.now();
  btn.classList.add('recording');
  btn.title = 'Click to stop recording';

  // Timer display
  const timer = document.createElement('span');
  timer.className = 'mic-timer';
  timer.id = 'mic-timer';
  btn.appendChild(timer);
  MIC.timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - MIC.startTime) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    timer.textContent = `${m}:${s}`;
  }, 500);

  // Auto-stop after 5 minutes
  setTimeout(() => { if (MIC.isRecording) stopRecording(); }, 5 * 60 * 1000);
}

function stopRecording() {
  if (!MIC.isRecording || !MIC.mediaRecorder) return;
  MIC.isRecording = false;
  MIC.mediaRecorder.stop();
  clearInterval(MIC.timerInterval);

  const btn = $('btn-mic');
  btn.classList.remove('recording');
  btn.classList.add('transcribing');
  btn.title = 'Transcribing…';

  const timer = $('mic-timer');
  if (timer) timer.remove();
}

async function transcribeAndInsert(blob) {
  const btn = $('btn-mic');
  try {
    const ext = blob.type.includes('webm') ? 'webm' : blob.type.includes('ogg') ? 'ogg' : 'wav';
    const file = new File([blob], `recording.${ext}`, { type: blob.type });
    const form = new FormData();
    form.append('file', file);

    const res = await fetch(`${API_BASE}/sessions/${STATE.currentSessionId}/transcribe`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${STATE.token}` },
      body: form,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    if (data.text) {
      // Insert transcribed text into the message input
      const textarea = $('message-input');
      const existing = textarea.value;
      textarea.value = existing ? (existing + ' ' + data.text) : data.text;
      autoResizeTextarea(textarea);
      syncMirror();
      $('btn-send').disabled = false;
      textarea.focus();
      renderSystemMessage(`🎤 Transcribed via ${data.backend} (${formatFileSize(data.size)})`);
    } else {
      renderSystemMessage('⚠️ No speech detected in recording.');
    }
  } catch (err) {
    renderSystemMessage('❌ Transcription failed: ' + err.message);
  } finally {
    btn.classList.remove('transcribing');
    btn.title = MIC.canRecord ? 'Click to record voice' : 'Upload audio file for transcription';
  }
}

_initMic();
document.addEventListener('DOMContentLoaded', _initMic);

// ─── File Viewer ──────────────────────────────────────────────────────────────

const _FV_IMAGE_EXTS = /\.(png|jpe?g|gif|webp|svg|ico|bmp)$/i;
const _FV_MD_EXTS    = /\.(md|markdown)$/i;
const _FV_HTML_EXTS  = /\.(html?|xml)$/i;
const _FV_PREVIEW_EXTS = /\.(md|markdown|html?)$/i;

// Regex to match absolute file paths in text
const _FV_PATH_RE = /(?:^|[\s"'`(,;])(\/(opt|tmp|home)\/[^\s"'`)<>,;|]+\.[a-zA-Z0-9]{1,10})(?=[\s"'`),;|]|$)/g;

function openFileViewer(filePath) {
  STATE.fileViewerPath = filePath;
  STATE.fileViewerData = null;
  STATE.fileViewerRaw = false;
  STATE.fileViewerOpen = true;

  const panel = $('file-viewer-panel');
  panel.classList.remove('fv-hidden');

  // Update UI
  $('fv-filename').textContent = filePath.split('/').pop();
  $('fv-filename').title = filePath;
  $('fv-meta').innerHTML = '';
  $('fv-content').innerHTML = '<div class="fv-loading"><div class="fv-spinner"></div>Loading…</div>';

  // Update raw button state
  const rawBtn = $('btn-fv-raw');
  rawBtn.classList.remove('active');
  rawBtn.textContent = 'Raw';
  // Only show toggle for md/html
  rawBtn.style.display = _FV_PREVIEW_EXTS.test(filePath) ? '' : 'none';

  fetchFileContent(filePath);
}

function closeFileViewer() {
  STATE.fileViewerOpen = false;
  STATE.fileViewerPath = null;
  STATE.fileViewerData = null;
  $('file-viewer-panel').classList.add('fv-hidden');
}

function toggleFileViewerMode() {
  STATE.fileViewerRaw = !STATE.fileViewerRaw;
  const rawBtn = $('btn-fv-raw');
  if (STATE.fileViewerRaw) {
    rawBtn.classList.add('active');
    rawBtn.textContent = 'Preview';
  } else {
    rawBtn.classList.remove('active');
    rawBtn.textContent = 'Raw';
  }
  if (STATE.fileViewerData) renderFileContent(STATE.fileViewerData);
}

async function fetchFileContent(filePath) {
  try {
    const isImage = _FV_IMAGE_EXTS.test(filePath);
    if (isImage) {
      // Fetch as blob for images
      const url = `${API_BASE}/files/view/raw?path=${encodeURIComponent(filePath)}`;
      const res = await fetch(url, {
        headers: STATE.token ? { 'Authorization': `Bearer ${STATE.token}` } : {},
      });
      if (res.status === 401) { clearAuth(); showAuthView(); return; }
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail);
      }
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const data = {
        type: 'image', name: filePath.split('/').pop(),
        path: filePath, size: blob.size, mime: blob.type, blobUrl,
      };
      STATE.fileViewerData = data;
      renderFileContent(data);
    } else {
      // Fetch as JSON (text content)
      const url = `${API_BASE}/files/view?path=${encodeURIComponent(filePath)}`;
      const res = await fetch(url, {
        headers: STATE.token ? { 'Authorization': `Bearer ${STATE.token}` } : {},
      });
      if (res.status === 401) { clearAuth(); showAuthView(); return; }
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail);
      }
      const data = await res.json();
      STATE.fileViewerData = data;
      renderFileContent(data);
    }
  } catch (err) {
    $('fv-content').innerHTML = `<div class="fv-error">❌ ${escHtml(err.message)}</div>`;
  }
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function renderFileContent(data) {
  // Meta bar
  const metaEl = $('fv-meta');
  const pathHtml = `<span class="fv-meta-path" title="Click to copy" onclick="navigator.clipboard.writeText('${escHtml(data.path)}')">${escHtml(data.path)}</span>`;
  const sizeHtml = `<span class="fv-meta-pill">${formatFileSize(data.size)}</span>`;
  const langHtml = data.language ? `<span class="fv-meta-pill">${escHtml(data.language)}</span>` : '';
  metaEl.innerHTML = pathHtml + sizeHtml + langHtml;

  const contentEl = $('fv-content');

  if (data.type === 'image') {
    contentEl.innerHTML = `<div class="fv-image-wrap"><img src="${data.blobUrl}" alt="${escHtml(data.name)}" /></div>`;
    return;
  }

  // Text content
  const isMarkdown = _FV_MD_EXTS.test(data.name);
  const isHtml = _FV_HTML_EXTS.test(data.name);
  const showPreview = (isMarkdown || isHtml) && !STATE.fileViewerRaw;

  if (showPreview) {
    const wrap = document.createElement('div');
    wrap.className = 'fv-preview';
    if (isMarkdown) {
      try {
        wrap.innerHTML = marked.parse(data.content, { breaks: true });
        wrap.querySelectorAll('pre code').forEach(block => {
          if (window.hljs) hljs.highlightElement(block);
        });
      } catch (_) {
        wrap.textContent = data.content;
      }
    } else {
      // HTML → render in sandboxed iframe to prevent XSS
      const iframe = document.createElement('iframe');
      iframe.sandbox = 'allow-same-origin';
      const minH = isMobileViewport() ? 240 : 400;
      iframe.style.cssText = `width:100%;border:none;min-height:${minH}px;background:#fff`;
      wrap.appendChild(iframe);
      setTimeout(() => {
        const doc = iframe.contentDocument || iframe.contentWindow.document;
        doc.open(); doc.write(data.content); doc.close();
        iframe.style.height = doc.documentElement.scrollHeight + 'px';
      }, 0);
    }
    // Make file paths in preview clickable too
    linkifyFilePaths(wrap);
    contentEl.innerHTML = '';
    contentEl.appendChild(wrap);
  } else {
    // Raw view with line numbers
    const lines = data.content.split('\n');
    const wrap = document.createElement('div');
    wrap.className = 'fv-raw';
    for (const line of lines) {
      const span = document.createElement('span');
      span.className = 'fv-raw-line';
      span.textContent = line || ' ';
      wrap.appendChild(span);
    }
    contentEl.innerHTML = '';
    contentEl.appendChild(wrap);
  }
}

/**
 * Scan a DOM element for file paths and make them clickable.
 * Works on both message bubbles and file viewer previews.
 * Handles paths inside <code> tags (from markdown backticks).
 */
function linkifyFilePaths(container) {
  // First pass: handle <code> elements whose entire content is a file path
  const codeEls = Array.from(container.querySelectorAll('code'));
  for (const codeEl of codeEls) {
    // Skip code blocks inside <pre> (multi-line code)
    if (codeEl.closest('pre')) continue;
    // Skip if already linkified
    if (codeEl.closest('.file-link')) continue;
    const text = codeEl.textContent.trim();
    if (/^\/(opt|tmp|home)\/[^\s"'`)<>,;|*?]+\.[a-zA-Z0-9]{1,10}$/.test(text)) {
      const link = document.createElement('span');
      link.className = 'file-link';
      link.textContent = text;
      link.title = 'Click to view file';
      link.dataset.filepath = text;
      link.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        openFileViewer(e.target.closest('.file-link').dataset.filepath);
      });
      codeEl.replaceWith(link);
    }
  }

  // Second pass: handle bare paths in text nodes (not in code/pre/a/script)
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
  const textNodes = [];
  let node;
  while ((node = walker.nextNode())) textNodes.push(node);

  for (const textNode of textNodes) {
    const parent = textNode.parentElement;
    if (!parent) continue;
    const tag = parent.tagName;
    if (tag === 'A' || tag === 'PRE' || tag === 'SCRIPT' || tag === 'STYLE') continue;
    if (parent.classList.contains('file-link')) continue;
    // Skip code blocks
    if (parent.closest('pre')) continue;

    const text = textNode.textContent;
    const re = /(\/(opt|tmp|home)\/[^\s"'`)<>,;|*?]+\.[a-zA-Z0-9]{1,10})/g;
    let match;
    const parts = [];
    let lastIndex = 0;
    while ((match = re.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push(document.createTextNode(text.slice(lastIndex, match.index)));
      }
      const link = document.createElement('span');
      link.className = 'file-link';
      link.textContent = match[1];
      link.title = 'Click to view file';
      link.dataset.filepath = match[1];
      link.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        openFileViewer(e.target.closest('.file-link').dataset.filepath);
      });
      parts.push(link);
      lastIndex = re.lastIndex;
    }
    if (parts.length > 0) {
      if (lastIndex < text.length) {
        parts.push(document.createTextNode(text.slice(lastIndex)));
      }
      const frag = document.createDocumentFragment();
      parts.forEach(p => frag.appendChild(p));
      textNode.replaceWith(frag);
    }
  }
}

// Wire up file viewer buttons
// Modules are deferred, so DOM is already ready. Wire up immediately and also on DOMContentLoaded as fallback.
function _initFileViewer() {
  const closeBtn = $('btn-fv-close');
  if (closeBtn && !closeBtn._fvBound) {
    closeBtn._fvBound = true;
    closeBtn.addEventListener('click', closeFileViewer);
  }
  const rawBtn = $('btn-fv-raw');
  if (rawBtn && !rawBtn._fvBound) {
    rawBtn._fvBound = true;
    rawBtn.addEventListener('click', toggleFileViewerMode);
  }
  // Close on Escape key
  if (!window._fvEscBound) {
    window._fvEscBound = true;
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && STATE.fileViewerOpen) {
        closeFileViewer();
      }
    });
  }
}
_initFileViewer();
document.addEventListener('DOMContentLoaded', _initFileViewer);

window.openFileViewer = openFileViewer;
window.linkifyFilePaths = linkifyFilePaths;

// ─── Mobile Tab Navigation ────────────────────────────────────────────────────
// Allows users to switch between Chat, Queue views on mobile.
// Desktop layout is unchanged (panels visible side-by-side).

let _mobileActiveTab = 'chat';
let _mobileQueueWasMinimized = true; // track queue-minimized state to restore on close

function switchMobileTab(tabName) {
  if (window.innerWidth > 768) return; // no-op on desktop

  _mobileActiveTab = tabName;

  // Update tab button active states
  document.querySelectorAll('.mobile-tab').forEach(t => {
    const isActive = t.dataset.tab === tabName;
    t.classList.toggle('active', isActive);
    t.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });

  const panel = $('request-queue-panel');
  const chatPanel = $('chat-panel');
  if (!panel || !chatPanel) return;

  if (tabName === 'chat') {
    // Hide panel overlay, restore minimized state
    panel.classList.remove('mobile-panel-active', 'mobile-show-queue-only');
    chatPanel.classList.remove('hidden');
    if (_mobileQueueWasMinimized) panel.classList.add('queue-minimized');
  } else {
    // Save current minimized state before showing
    _mobileQueueWasMinimized = panel.classList.contains('queue-minimized');
    panel.classList.remove('queue-minimized');
    panel.classList.add('mobile-panel-active');
    chatPanel.classList.add('hidden');

    if (tabName === 'queue') {
      panel.classList.add('mobile-show-queue-only');
      renderQueuePanel();
    }
  }
}

function updateMobileBadges() {
  // Keep mobile badge counts in sync with queue counters
  const queueBadge = $('mobile-queue-badge');

  if (queueBadge) {
    const count = STATE.requestQueue ? STATE.requestQueue.length : 0;
    queueBadge.textContent = count;
    queueBadge.classList.toggle('hidden', count === 0);
  }

}

function initMobileTabs() {
  document.querySelectorAll('.mobile-tab').forEach(tab => {
    tab.addEventListener('click', () => switchMobileTab(tab.dataset.tab));
  });

}

// Wire up on DOMContentLoaded and immediately (module scripts defer automatically)
if (document.readyState !== 'loading') {
  initMobileTabs();
} else {
  document.addEventListener('DOMContentLoaded', initMobileTabs);
}

// Patch renderQueuePanel to also update mobile badges
const _origRenderQueuePanel = renderQueuePanel;
window._mobileUpdateBadges = updateMobileBadges;
// Hook into queue count updates via MutationObserver on the queue count element
document.addEventListener('DOMContentLoaded', () => {
  const queueCountEl = $('queue-count');
  if (queueCountEl && window.MutationObserver) {
    new MutationObserver(updateMobileBadges).observe(queueCountEl, { childList: true, characterData: true, subtree: true });
  }
  updateMobileBadges();
});

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Wee Canvas Panel ───────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

const _canvasSessions = new Map(); // sessionId → { ws, components, connected, name }
const _dismissedCanvasSessions = new Set(); // sessions manually closed by user — poller skips these
let _activeCanvasSession = null;
let _canvasPanelOpen = false;
let _closedCanvasExpanded = false;

// ── Canvas node registry (for partial updates) ──────────────────────────────
const _canvasNodeRegistry = new Map();

// ── Panel toggle ─────────────────────────────────────────────────────────────

function toggleCanvasPanel() {
  if (_canvasPanelOpen) closeCanvasPanel();
  else openCanvasPanel();
}

function openCanvasPanel() {
  const panel = $('canvas-panel');
  if (!panel) return;
  panel.classList.remove('canvas-hidden');
  panel.classList.add('canvas-open');
  _canvasPanelOpen = true;
}

function closeCanvasPanel() {
  const panel = $('canvas-panel');
  if (!panel) return;
  panel.classList.add('canvas-hidden');
  panel.classList.remove('canvas-open');
  _canvasPanelOpen = false;
}

// ── Session management ───────────────────────────────────────────────────────

function openCanvasSession(sessionId) {
  if (!_canvasSessions.has(sessionId)) {
    _connectCanvasWS(sessionId);
  }
  _activeCanvasSession = sessionId;
  _renderCanvasTabs();
  _renderActiveCanvas();
  if (!_canvasPanelOpen) openCanvasPanel();
}

function closeCanvasSession(sessionId) {
  const sess = _canvasSessions.get(sessionId);
  if (sess && sess.ws) {
    try { sess.ws.close(); } catch(e) {}
  }
  _canvasSessions.delete(sessionId);
  _dismissedCanvasSessions.add(sessionId); // prevent poller from re-opening
  if (_activeCanvasSession === sessionId) {
    const keys = [..._canvasSessions.keys()];
    _activeCanvasSession = keys.length ? keys[keys.length - 1] : null;
  }
  // Persist session to disk via backend
  fetch(`${API_BASE}/canvas/sessions/${sessionId}/close`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${STATE.token}` }
  }).catch(() => {});
  _renderCanvasTabs();
  _renderActiveCanvas();
  _updateCanvasBadge();
  if (_canvasSessions.size === 0) {
    const empty = $('canvas-empty');
    if (empty) empty.style.display = '';
  }
}

function _connectCanvasWS(sessionId) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl = `${proto}://${location.host}/canvas/ws?session=${sessionId}`;
  const sess = { ws: null, components: [], connected: false, name: null };
  _canvasSessions.set(sessionId, sess);

  const ws = new WebSocket(wsUrl);
  sess.ws = ws;

  ws.onopen = () => {
    sess.connected = true;
    _renderCanvasTabs();
  };

  ws.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); } catch(e) { return; }
    _handleCanvasMessage(sessionId, msg);
  };

  ws.onclose = () => {
    sess.connected = false;
    // Auto-reconnect after 3s if session still tracked
    if (_canvasSessions.has(sessionId)) {
      setTimeout(() => {
        if (_canvasSessions.has(sessionId)) {
          _canvasSessions.delete(sessionId);
          _connectCanvasWS(sessionId);
          if (_activeCanvasSession === sessionId) _renderActiveCanvas();
        }
      }, 3000);
    }
  };

  ws.onerror = () => { sess.connected = false; };
}

function _handleCanvasMessage(sessionId, msg) {
  const sess = _canvasSessions.get(sessionId);
  if (!sess) return;

  if (msg.type === 'render' || msg.type === 'restore') {
    sess.components = msg.components || [];
    if (_activeCanvasSession === sessionId) _renderActiveCanvas();
  } else if (msg.type === 'update') {
    _canvasApplyUpdate(sess.components, msg.node_id, msg.changes);
    if (_activeCanvasSession === sessionId) {
      // Try partial update first
      const el = _canvasNodeRegistry.get(msg.node_id);
      if (el && el._compData) {
        Object.assign(el._compData, msg.changes);
        const newEl = _canvasRenderComponent(el._compData);
        if (newEl && el.parentNode) {
          el.parentNode.replaceChild(newEl, el);
          _canvasNodeRegistry.set(msg.node_id, newEl);
        }
      } else {
        _renderActiveCanvas();
      }
    }
  } else if (msg.type === 'clear') {
    sess.components = [];
    if (_activeCanvasSession === sessionId) _renderActiveCanvas();
  }
}

function _canvasApplyUpdate(components, nodeId, changes) {
  for (const comp of components) {
    if (typeof comp !== 'object' || !comp) continue;
    if (comp.id === nodeId) { Object.assign(comp, changes); return true; }
    for (const key of ['children', 'items', 'columns', 'steps', 'rows', 'metrics', 'fields']) {
      const arr = comp[key];
      if (Array.isArray(arr) && _canvasApplyUpdate(arr, nodeId, changes)) return true;
    }
  }
  return false;
}

// ── Tab rendering ────────────────────────────────────────────────────────────

function _renderCanvasTabs() {
  const bar = $('canvas-tab-bar');
  if (!bar) return;
  bar.innerHTML = '';

  for (const [sid, sessData] of _canvasSessions) {
    const tab = document.createElement('div');
    tab.className = 'canvas-tab' + (sid === _activeCanvasSession ? ' active' : '');

    const label = document.createElement('span');
    label.className = 'canvas-tab-label';
    label.textContent = sessData.name || (sid.length > 8 ? sid.slice(0, 8) : sid);
    tab.appendChild(label);

    // Double-click to rename
    label.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      _startCanvasRename(tab, label, sid, sessData);
    });

    const closeBtn = document.createElement('button');
    closeBtn.className = 'canvas-tab-close';
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', (e) => { e.stopPropagation(); closeCanvasSession(sid); });
    tab.appendChild(closeBtn);

    tab.addEventListener('click', () => {
      _activeCanvasSession = sid;
      _renderCanvasTabs();
      _renderActiveCanvas();
    });

    bar.appendChild(tab);
  }
}

function _startCanvasRename(tab, label, sessionId, sessData) {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'canvas-tab-rename';
  input.value = sessData.name || '';
  input.placeholder = sessionId.slice(0, 8);

  label.replaceWith(input);
  input.focus();
  input.select();

  function save() {
    const newName = input.value.trim();
    sessData.name = newName || null;
    // Persist name to backend
    fetch(`${API_BASE}/canvas/sessions/${sessionId}/name`, {
      method: 'PATCH',
      headers: {
        'Authorization': `Bearer ${STATE.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ name: newName }),
    }).catch(() => {});
    _renderCanvasTabs();
  }

  function cancel() {
    _renderCanvasTabs();
  }

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); save(); }
    if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  });
  input.addEventListener('blur', save);
}

// ── Canvas content rendering ─────────────────────────────────────────────────

function _renderActiveCanvas() {
  const content = $('canvas-content');
  const empty = $('canvas-empty');
  if (!content) return;

  const sess = _activeCanvasSession ? _canvasSessions.get(_activeCanvasSession) : null;

  if (!sess || !sess.components || sess.components.length === 0) {
    // Show empty state
    content.innerHTML = '';
    if (empty) { content.appendChild(empty); empty.style.display = ''; }
    return;
  }

  if (empty) empty.style.display = 'none';
  _canvasNodeRegistry.clear();
  content.innerHTML = '';

  for (const comp of sess.components) {
    const el = _canvasRenderComponent(comp);
    if (el) content.appendChild(el);
  }

  // Re-render mermaid diagrams
  const flowcharts = content.querySelectorAll('.mermaid-src');
  flowcharts.forEach(node => _canvasRenderMermaid(node));
}

function _canvasRenderComponent(comp) {
  if (!comp || typeof comp !== 'object') return null;
  const el = _canvasDispatch(comp);
  if (!el) return null;
  if (comp.id) {
    el.dataset.nodeId = comp.id;
    _canvasNodeRegistry.set(comp.id, el);
  }
  el._compData = comp;
  el.classList.add('anim-in');
  return el;
}

function _canvasDispatch(comp) {
  switch (comp.type) {
    case 'board':       return _cvBoard(comp);
    case 'card':        return _cvCard(comp);
    case 'grid':        return _cvGrid(comp);
    case 'row':         return _cvRow(comp);
    case 'col':         return _cvCol(comp);
    case 'table':       return _cvTable(comp);
    case 'chart_bar':   case 'chart_line': case 'chart_pie':
    case 'chart_doughnut': case 'chart_radar': case 'chart_polar':
      return _cvChart(comp);
    case 'metric':      return _cvMetric(comp);
    case 'progress':    return _cvProgress(comp);
    case 'badge':       return _cvBadge(comp);
    case 'log':         return _cvLog(comp);
    case 'button':      return _cvButton(comp);
    case 'form':        return _cvForm(comp);
    case 'heading':     return _cvHeading(comp);
    case 'text':        return _cvText(comp);
    case 'markdown':    return _cvMarkdown(comp);
    case 'html':        return _cvHtml(comp);
    case 'list':        return _cvList(comp);
    case 'divider':     { const hr = document.createElement('hr'); hr.className = 'glass-divider'; return hr; }
    case 'flowchart':   return _cvFlowchart(comp);
    case 'code':        return _cvCode(comp);
    case '__board_item__': return _cvBoardItem(comp);
    default: {
      const d = document.createElement('div');
      d.style.cssText = 'color:var(--text-muted);font-size:11px;';
      d.textContent = `[unknown: ${comp.type}]`;
      return d;
    }
  }
}

// ── Component renderers ─────────────────────────────────────────────────────

function _cvBoard(comp) {
  const w = document.createElement('div');
  w.className = 'board-wrap glass-panel';
  for (const col of (comp.columns || [])) {
    const c = document.createElement('div');
    c.className = 'board-col';
    if (col.id) { c.dataset.nodeId = col.id; _canvasNodeRegistry.set(col.id, c); }
    const t = document.createElement('div');
    t.className = 'board-col-title';
    t.textContent = col.title || '';
    c.appendChild(t);
    for (const item of (col.items || [])) {
      const el = document.createElement('div');
      el.className = 'board-item anim-in';
      if (item.id) { el.dataset.nodeId = item.id; _canvasNodeRegistry.set(item.id, el); el._compData = Object.assign({}, item, {type: '__board_item__'}); }
      el.textContent = item.title || item.name || '';
      if (item.status) {
        const colors = { done:'#3ecf8e', running:'#f5c542', pending:'rgba(255,255,255,0.3)', error:'#ff5f6d' };
        el.style.borderLeft = `3px solid ${colors[item.status] || 'rgba(255,255,255,0.2)'}`;
      }
      c.appendChild(el);
    }
    w.appendChild(c);
  }
  return w;
}

function _cvBoardItem(item) {
  const el = document.createElement('div');
  el.className = 'board-item anim-in';
  el.textContent = item.title || item.name || '';
  const colors = { done:'#3ecf8e', running:'#f5c542', pending:'rgba(255,255,255,0.3)', error:'#ff5f6d' };
  el.style.borderLeft = item.status
    ? `3px solid ${colors[item.status] || 'rgba(255,255,255,0.2)'}`
    : '3px solid rgba(255,255,255,0.1)';
  return el;
}

function _cvCard(comp) {
  const w = document.createElement('div');
  w.className = 'glass-card';
  if (comp.title) {
    const h = document.createElement('div');
    h.style.cssText = 'font-weight:600;font-size:13px;margin-bottom:8px;';
    h.textContent = comp.title;
    w.appendChild(h);
  }
  for (const ch of (comp.children || [])) {
    const el = _canvasRenderComponent(ch);
    if (el) w.appendChild(el);
  }
  if (comp.content && typeof comp.content === 'string') {
    const p = document.createElement('p');
    p.style.cssText = 'font-size:12px;color:var(--text-secondary);';
    p.textContent = comp.content;
    w.appendChild(p);
  }
  return w;
}

function _cvGrid(comp) {
  const w = document.createElement('div');
  w.className = 'c-grid';
  w.style.gridTemplateColumns = `repeat(${comp.cols || 2}, 1fr)`;
  for (const ch of (comp.children || [])) { const el = _canvasRenderComponent(ch); if (el) w.appendChild(el); }
  return w;
}

function _cvRow(comp) {
  const w = document.createElement('div');
  w.className = 'c-row';
  for (const ch of (comp.children || [])) { const el = _canvasRenderComponent(ch); if (el) w.appendChild(el); }
  return w;
}

function _cvCol(comp) {
  const w = document.createElement('div');
  w.className = 'c-col';
  for (const ch of (comp.children || [])) { const el = _canvasRenderComponent(ch); if (el) w.appendChild(el); }
  return w;
}

function _cvTable(comp) {
  const w = document.createElement('div');
  w.className = 'glass-panel';
  if (comp.label || comp.title) {
    const h = document.createElement('div');
    h.style.cssText = 'font-weight:600;font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px;';
    h.textContent = comp.label || comp.title;
    w.appendChild(h);
  }
  const scrollWrap = document.createElement('div');
  scrollWrap.className = 'glass-table-wrap';
  const table = document.createElement('table');
  table.className = 'glass-table';
  if (comp.headers?.length) {
    const thead = document.createElement('thead');
    const tr = document.createElement('tr');
    for (const h of comp.headers) { const th = document.createElement('th'); th.textContent = h; tr.appendChild(th); }
    thead.appendChild(tr);
    table.appendChild(thead);
  }
  const tbody = document.createElement('tbody');
  for (const row of (comp.rows || [])) {
    const tr = document.createElement('tr');
    const cells = Array.isArray(row) ? row : Object.values(row);
    for (const cell of cells) { const td = document.createElement('td'); td.textContent = cell; tr.appendChild(td); }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  scrollWrap.appendChild(table);
  w.appendChild(scrollWrap);
  return w;
}

function _cvChart(comp) {
  const w = document.createElement('div');
  w.className = 'glass-panel';
  if (comp.label || comp.title) {
    const h = document.createElement('div');
    h.style.cssText = 'font-weight:600;font-size:12px;color:var(--text-muted);margin-bottom:10px;';
    h.textContent = comp.label || comp.title;
    w.appendChild(h);
  }
  const canvas = document.createElement('canvas');
  canvas.style.maxHeight = '250px';
  w.appendChild(canvas);
  setTimeout(() => {
    if (typeof Chart === 'undefined') return;
    const typeMap = { chart_bar:'bar', chart_line:'line', chart_pie:'pie', chart_doughnut:'doughnut', chart_radar:'radar', chart_polar:'polarArea' };
    const chartType = typeMap[comp.type] || 'line';
    const datasets = (comp.datasets || []).map((ds, i) => ({
      ...ds,
      borderColor: ds.borderColor || ['#3ecf8e','#f5c542','#7fb5ff','#ff8888','#c084fc'][i % 5],
      backgroundColor: ds.backgroundColor || (chartType === 'line' ? 'transparent' : undefined),
    }));
    new Chart(canvas, {
      type: chartType,
      data: { labels: comp.labels || [], datasets },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: 'rgba(255,255,255,0.6)' } } },
        scales: chartType === 'line' || chartType === 'bar' ? {
          x: { ticks: { color: 'rgba(255,255,255,0.4)' }, grid: { color: 'rgba(255,255,255,0.06)' } },
          y: { ticks: { color: 'rgba(255,255,255,0.4)' }, grid: { color: 'rgba(255,255,255,0.06)' } }
        } : undefined
      }
    });
  }, 50);
  return w;
}

function _cvMetric(comp) {
  const w = document.createElement('div');
  w.className = 'glass-card';
  w.style.cssText = 'min-width:100px;text-align:center;';
  const val = document.createElement('div');
  val.className = 'metric-value';
  if (comp.trend === 'up') val.classList.add('trend-up');
  if (comp.trend === 'down') val.classList.add('trend-down');
  val.textContent = comp.value || '—';
  if (comp.trend === 'up') { const s = document.createElement('span'); s.textContent = ' ↑'; s.className = 'trend-up'; val.appendChild(s); }
  if (comp.trend === 'down') { const s = document.createElement('span'); s.textContent = ' ↓'; s.className = 'trend-down'; val.appendChild(s); }
  w.appendChild(val);
  const lbl = document.createElement('div');
  lbl.className = 'metric-label';
  lbl.textContent = comp.label || '';
  w.appendChild(lbl);
  return w;
}

function _cvProgress(comp) {
  const w = document.createElement('div');
  w.className = 'glass-panel';
  w.style.padding = '14px';
  const top = document.createElement('div');
  top.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;';
  const label = document.createElement('span');
  label.style.cssText = 'font-size:12px;color:var(--text-secondary);';
  label.textContent = comp.label || '';
  top.appendChild(label);
  const pct = Math.max(0, Math.min(100, comp.pct || 0));
  const pctLabel = document.createElement('span');
  pctLabel.style.cssText = 'font-size:12px;font-weight:600;color:var(--accent);';
  pctLabel.textContent = `${pct}%`;
  top.appendChild(pctLabel);
  w.appendChild(top);
  const track = document.createElement('div');
  track.className = 'progress-wrap';
  const bar = document.createElement('div');
  bar.className = 'progress-bar';
  bar.style.width = `${pct}%`;
  track.appendChild(bar);
  w.appendChild(track);
  return w;
}

function _cvBadge(comp) {
  const el = document.createElement('span');
  el.className = `badge badge-${comp.variant || 'neutral'}`;
  el.textContent = comp.text || comp.label || '';
  return el;
}

function _cvLog(comp) {
  const w = document.createElement('div');
  w.className = 'glass-panel';
  w.style.padding = '10px';
  if (comp.label) {
    const h = document.createElement('div');
    h.style.cssText = 'font-size:10px;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.06em;';
    h.textContent = comp.label;
    w.appendChild(h);
  }
  const pre = document.createElement('div');
  pre.className = 'log-area';
  const lines = comp.lines || comp.content || '';
  pre.textContent = Array.isArray(lines) ? lines.join('\n') : String(lines);
  requestAnimationFrame(() => { pre.scrollTop = pre.scrollHeight; });
  w.appendChild(pre);
  return w;
}

function _cvButton(comp) {
  const btn = document.createElement('button');
  const vmap = { primary:'btn-primary', ghost:'btn-ghost', danger:'btn-danger', gold:'btn-gold', secondary:'btn-ghost' };
  btn.className = `btn ${vmap[comp.variant] || 'btn-primary'}`;
  btn.textContent = comp.label || comp.text || 'Button';
  if (comp.disabled) btn.disabled = true;
  btn.addEventListener('click', () => {
    if (comp.action_id) _canvasSendAction(comp.action_id, {});
  });
  return btn;
}

function _cvForm(comp) {
  const w = document.createElement('div');
  w.className = 'glass-panel';
  const fieldValues = {};
  for (const field of (comp.fields || [])) {
    const fw = document.createElement('div');
    fw.style.marginBottom = '12px';
    if (field.label) {
      const lbl = document.createElement('label');
      lbl.style.cssText = 'display:block;font-size:11px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em;';
      lbl.textContent = field.label;
      fw.appendChild(lbl);
    }
    const inputType = field.type || field.input_type || 'text';
    if (inputType === 'select' || (field.options && field.options.length)) {
      const sel = document.createElement('select');
      sel.className = 'glass-select';
      for (const opt of (field.options || [])) {
        const o = document.createElement('option');
        o.value = o.textContent = opt;
        if (field.default && opt === field.default) o.selected = true;
        sel.appendChild(o);
      }
      sel.addEventListener('change', () => { fieldValues[field.name] = sel.value; });
      fieldValues[field.name] = sel.value;
      fw.appendChild(sel);
    } else if (inputType === 'checkbox') {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.style.accentColor = 'var(--accent)';
      cb.checked = !!field.default;
      cb.addEventListener('change', () => { fieldValues[field.name] = cb.checked; });
      fieldValues[field.name] = cb.checked;
      row.appendChild(cb);
      fw.innerHTML = '';
      fw.appendChild(row);
    } else {
      const inp = document.createElement('input');
      inp.type = inputType === 'number' ? 'number' : 'text';
      inp.className = 'glass-input';
      inp.placeholder = field.placeholder || '';
      inp.value = field.default || '';
      inp.addEventListener('input', () => { fieldValues[field.name] = inp.value; });
      fieldValues[field.name] = inp.value;
      fw.appendChild(inp);
    }
    w.appendChild(fw);
  }
  if (comp.actions?.length) {
    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:14px;';
    for (const act of comp.actions) {
      const btn = document.createElement('button');
      const vmap = { primary:'btn-primary', ghost:'btn-ghost', danger:'btn-danger' };
      btn.className = `btn ${vmap[act.variant] || 'btn-primary'}`;
      btn.textContent = act.label;
      btn.addEventListener('click', () => _canvasSendAction(act.action_id, { ...fieldValues }));
      btnRow.appendChild(btn);
    }
    w.appendChild(btnRow);
  }
  return w;
}

function _cvHeading(comp) {
  const level = Math.min(4, Math.max(1, comp.level || 2));
  const el = document.createElement(`h${level}`);
  el.className = `c-h${level}`;
  el.textContent = comp.text || '';
  return el;
}

function _cvText(comp) {
  const el = document.createElement('p');
  el.style.cssText = `font-size:13px;line-height:1.6;color:${comp.muted ? 'var(--text-muted)' : 'var(--text-secondary)'};`;
  el.textContent = comp.text || comp.content || '';
  return el;
}

function _cvMarkdown(comp) {
  const el = document.createElement('div');
  el.style.cssText = 'font-size:13px;line-height:1.7;color:var(--text-secondary);';
  const md = comp.content || comp.text || '';
  if (typeof marked !== 'undefined') {
    const html = marked.parse(md);
    el.innerHTML = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html) : html;
  } else {
    el.textContent = md;
  }
  return el;
}

function _cvHtml(comp) {
  const el = document.createElement('div');
  const htmlContent = comp.content || comp.html || '';
  const iframe = document.createElement('iframe');
  if (comp.src) {
    // External URL mode: use src attribute with full permissions for interactive embeds
    iframe.sandbox = 'allow-scripts allow-same-origin allow-forms allow-popups allow-pointer-lock';
    iframe.src = comp.src;
    iframe.allow = 'fullscreen';
  } else {
    // Inline HTML mode: sandboxed srcdoc
    iframe.sandbox = 'allow-scripts';
    iframe.srcdoc = htmlContent;
  }
  iframe.style.cssText = 'width:100%;border:none;border-radius:8px;background:transparent;';
  iframe.style.height = (comp.height || 400) + 'px';
  window.addEventListener('message', function(evt) {
    if (evt.data && evt.data.type === 'resize' && evt.source === iframe.contentWindow) {
      iframe.style.height = (evt.data.height || 400) + 'px';
    }
  });
  el.appendChild(iframe);
  return el;
}

function _cvList(comp) {
  const el = document.createElement(comp.ordered ? 'ol' : 'ul');
  el.style.cssText = 'padding-left:18px;font-size:13px;color:var(--text-secondary);display:flex;flex-direction:column;gap:3px;';
  for (const item of (comp.items || [])) {
    const li = document.createElement('li');
    li.textContent = typeof item === 'string' ? item : item.text || '';
    el.appendChild(li);
  }
  return el;
}

function _cvFlowchart(comp) {
  const w = document.createElement('div');
  w.className = 'glass-panel mermaid-wrap';
  const md = document.createElement('div');
  md.className = 'mermaid-src';
  md.dataset.mermaidSrc = comp.content || 'flowchart TD\n  A[No content]';
  w.appendChild(md);
  setTimeout(() => _canvasRenderMermaid(md), 100);
  return w;
}

function _cvCode(comp) {
  const w = document.createElement('div');
  w.className = 'glass-panel';
  w.style.padding = '0';
  if (comp.label || comp.language) {
    const h = document.createElement('div');
    h.style.cssText = 'padding:6px 12px;font-size:10px;color:var(--text-muted);border-bottom:1px solid var(--glass-border);font-family:var(--font-mono);';
    h.textContent = comp.label || comp.language;
    w.appendChild(h);
  }
  const pre = document.createElement('div');
  pre.className = 'code-block';
  pre.textContent = comp.content || comp.code || '';
  w.appendChild(pre);
  return w;
}

// ── Mermaid renderer ─────────────────────────────────────────────────────────

async function _canvasRenderMermaid(el) {
  const src = (el.dataset.mermaidSrc || el.textContent || '').trim();
  if (!src || typeof mermaid === 'undefined') return;
  el.innerHTML = '';
  try {
    const id = 'mermaid-cv-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    const { svg } = await mermaid.render(id, src);
    el.innerHTML = svg;
  } catch (e) {
    el.style.cssText = 'color:var(--danger);font-family:var(--font-mono);font-size:11px;';
    el.textContent = 'Mermaid error: ' + e.message;
  }
}

// ── Send action back to agent ────────────────────────────────────────────────

function _canvasSendAction(actionId, formData) {
  if (!_activeCanvasSession) return;
  const sess = _canvasSessions.get(_activeCanvasSession);
  if (!sess || !sess.ws || sess.ws.readyState !== WebSocket.OPEN) return;
  sess.ws.send(JSON.stringify({
    type: 'action',
    action_id: actionId,
    data: formData,
    session_id: _activeCanvasSession,
    timestamp: new Date().toISOString(),
  }));
}

// ── Badge update ─────────────────────────────────────────────────────────────

function _updateCanvasBadge() {
  const badge = $('canvas-badge');
  if (!badge) return;
  const count = _canvasSessions.size;
  badge.textContent = count;
  if (count > 0) { badge.classList.remove('hidden'); }
  else { badge.classList.add('hidden'); }
}

// ── Poll for new sessions ────────────────────────────────────────────────────

async function _pollCanvasSessions() {
  try {
    const resp = await fetch(`${API_BASE}/canvas/sessions`, {
      headers: { 'Authorization': `Bearer ${STATE.token}` }
    });
    if (!resp.ok) return;
    const data = await resp.json();

    // Only auto-connect to active sessions that have components, are not already open,
    // and have not been manually dismissed by the user this session.
    for (const s of (data.sessions || [])) {
      if (s.status === 'active' && s.component_count > 0 && !_canvasSessions.has(s.session_id) && !_dismissedCanvasSessions.has(s.session_id)) {
        openCanvasSession(s.session_id);
      }
      // Sync name from server for active sessions
      if (s.status === 'active' && _canvasSessions.has(s.session_id) && s.name) {
        const local = _canvasSessions.get(s.session_id);
        if (!local.name && s.name) {
          local.name = s.name;
          _renderCanvasTabs();
        }
      }
    }

    // Update closed sessions list
    const closedSessions = (data.sessions || []).filter(s => s.status === 'closed');
    _renderClosedCanvasSessions(closedSessions);

    _updateCanvasBadge();
  } catch(e) { /* ignore polling errors */ }
}

// ── Closed sessions UI ───────────────────────────────────────────────────────

function _renderClosedCanvasSessions(closedSessions) {
  const container = $('canvas-closed-sessions');
  if (!container) return;

  if (!closedSessions || closedSessions.length === 0) {
    container.innerHTML = '';
    return;
  }

  const count = closedSessions.length;
  container.innerHTML = '';

  const toggleBtn = document.createElement('button');
  toggleBtn.className = 'canvas-closed-toggle';
  toggleBtn.textContent = `${count} closed session${count !== 1 ? 's' : ''} ${_closedCanvasExpanded ? '▲' : '▼'}`;
  toggleBtn.addEventListener('click', () => {
    _closedCanvasExpanded = !_closedCanvasExpanded;
    _renderClosedCanvasSessions(closedSessions);
  });
  container.appendChild(toggleBtn);

  const list = document.createElement('div');
  list.className = 'canvas-closed-list' + (_closedCanvasExpanded ? ' expanded' : '');

  for (const s of closedSessions) {
    const item = document.createElement('div');
    item.className = 'canvas-closed-item';

    const info = document.createElement('div');
    info.className = 'canvas-closed-item-info';

    const name = document.createElement('div');
    name.className = 'canvas-closed-item-name';
    name.textContent = s.name || (s.session_id.length > 8 ? s.session_id.slice(0, 8) : s.session_id);
    info.appendChild(name);

    if (s.closed_at) {
      const timeEl = document.createElement('div');
      timeEl.className = 'canvas-closed-item-time';
      timeEl.textContent = _formatClosedTime(s.closed_at);
      info.appendChild(timeEl);
    }

    item.appendChild(info);

    const restoreBtn = document.createElement('button');
    restoreBtn.className = 'canvas-closed-restore-btn';
    restoreBtn.textContent = 'Restore';
    restoreBtn.addEventListener('click', () => _restoreCanvasSession(s.session_id));
    item.appendChild(restoreBtn);

    list.appendChild(item);
  }

  container.appendChild(list);
}

function _formatClosedTime(ts) {
  try {
    const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString();
  } catch(e) { return ''; }
}

async function _restoreCanvasSession(sessionId) {
  try {
    const resp = await fetch(`${API_BASE}/canvas/sessions/${sessionId}/restore`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${STATE.token}` }
    });
    if (!resp.ok) return;
    // Remove from dismissed set so it can auto-open
    _dismissedCanvasSessions.delete(sessionId);
    // Open the session
    openCanvasSession(sessionId);
  } catch(e) { /* ignore */ }
}

// ── Init ─────────────────────────────────────────────────────────────────────

function _initCanvas() {
  // Edge tab toggle
  const edgeTab = $('canvas-edge-tab');
  if (edgeTab) edgeTab.addEventListener('click', toggleCanvasPanel);

  // Close button
  const closeBtn = $('btn-canvas-close');
  if (closeBtn) closeBtn.addEventListener('click', closeCanvasPanel);

  // Initialize mermaid for canvas
  if (typeof mermaid !== 'undefined') {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      themeVariables: {
        primaryColor: '#1a2a1a', primaryTextColor: '#fff',
        primaryBorderColor: '#3ecf8e', lineColor: '#3ecf8e',
        secondaryColor: '#0d2a1a', tertiaryColor: '#1a1a2e',
      },
    });
  }

  // Check URL for canvas parameter
  const urlParams = new URLSearchParams(location.search);
  const canvasParam = urlParams.get('canvas');
  if (canvasParam) {
    openCanvasSession(canvasParam);
  }

  // Poll for new canvas sessions every 5 seconds
  setInterval(_pollCanvasSessions, 5000);
}

// Make openCanvasSession available globally (for other parts of the app)
window.openCanvasSession = openCanvasSession;
window.toggleCanvasPanel = toggleCanvasPanel;

// Initialize when DOM ready
if (document.readyState !== 'loading') {
  _initCanvas();
} else {
  document.addEventListener('DOMContentLoaded', _initCanvas);
}

/* ── Settings & Logs Module ─────────────────────────────────────────────── */
(function initSettingsAndLogs() {
  /* ── Agent Settings Form Panel ─────────────────────────────────────────── */

  /** State for the settings panel */
  const ASF = {
    config: null,         // full { agents: [...] }
    originalPerms: null,  // deep copy of permissions before editing
    originalAgent: null,  // deep copy of full agent before editing
    selectedName: null,   // currently selected agent name
  };

  /* DOM refs */
  const modalSettings  = document.getElementById('modal-settings');
  const btnSettings    = document.getElementById('btn-settings');
  const btnSettClose   = document.getElementById('btn-settings-close');
  const btnSettCancel  = document.getElementById('btn-settings-cancel');
  const btnSettSave    = document.getElementById('btn-settings-save');
  const btnSettReload  = document.getElementById('btn-settings-reload');
  const btnSettAddAgent = document.getElementById('btn-settings-add-agent');
  const btnDeleteAgent = document.getElementById('asf-delete-agent');
  const asfSelector    = document.getElementById('asf-agent-selector');
  const asfError       = document.getElementById('asf-error');
  const asfSuccess     = document.getElementById('asf-success');
  const asfPermChanged = document.getElementById('asf-perm-changed');
  const asfModeBadge   = document.getElementById('asf-perm-mode-badge');
  const asfDirtyDot    = document.getElementById('asf-dirty-dot');

  /* Field refs */
  const F = {
    name:        () => document.getElementById('asf-name'),
    path:        () => document.getElementById('asf-path'),
    description: () => document.getElementById('asf-description'),
    primaryRuntime:   () => document.getElementById('asf-primary-runtime'),
    primaryModel:     () => document.getElementById('asf-primary-model'),
    fallbackRuntime:  () => document.getElementById('asf-fallback-runtime'),
    fallbackModel:    () => document.getElementById('asf-fallback-model'),
    maxConcurrent: () => document.getElementById('asf-max-concurrent'),
    permMode:    () => document.getElementById('asf-perm-mode'),
  };

  /** Permission list field ids → [section, key] mapping */
  const PERM_LISTS = {
    'asf-dir-allow-read':   ['directories', 'allow_read'],
    'asf-dir-allow-write':  ['directories', 'allow_write'],
    'asf-dir-deny':         ['directories', 'deny'],
    'asf-tools-allow':      ['tools', 'allow'],
    'asf-tools-deny':       ['tools', 'deny'],
    'asf-net-allow':        ['network', 'allow_urls'],
    'asf-net-deny':         ['network', 'deny_urls'],
    'asf-mcp-allow':        ['mcp', 'allow'],
    'asf-mcp-deny':         ['mcp', 'deny'],
  };

  function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function emptyPermissions() {
    return {
      mode: 'restricted',
      directories: { allow_read: [], allow_write: [], deny: [] },
      tools: { allow: ['*'], deny: [] },
      network: { allow_urls: ['*'], deny_urls: [] },
      mcp: { allow: [], deny: ['*'] },
    };
  }

  /* ── Banner helpers ─────────────────────────────────────────────────────── */
  function showErr(msg) {
    if (!asfError) return;
    asfError.textContent = msg;
    asfError.classList.remove('hidden');
    if (asfSuccess) asfSuccess.classList.add('hidden');
  }
  function showOk(msg) {
    if (!asfSuccess) return;
    asfSuccess.textContent = msg;
    asfSuccess.classList.remove('hidden');
    if (asfError) asfError.classList.add('hidden');
    setTimeout(() => asfSuccess && asfSuccess.classList.add('hidden'), 3500);
  }
  function clearBanners() {
    if (asfError)   asfError.classList.add('hidden');
    if (asfSuccess) asfSuccess.classList.add('hidden');
  }

  /* ── Tag list rendering ─────────────────────────────────────────────────── */
  /** Render a list of strings as tags in a container */
  function renderTagList(containerId, values, isDeny) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = '';
    (values || []).forEach((v, i) => {
      const isWild = v === '*';
      const tag = document.createElement('span');
      tag.className = 'asf-tag' + (isDeny ? ' asf-tag-deny' : (isWild ? ' asf-tag-wild' : ' asf-tag-allow'));
      tag.title = v;
      tag.innerHTML = `<span class="asf-tag-text">${escHtml(v)}</span><button type="button" class="asf-tag-rm" data-container="${containerId}" data-idx="${i}" aria-label="Remove ${escHtml(v)}">×</button>`;
      el.appendChild(tag);
    });
  }

  /** Read all tags from a container as an array of strings */
  function readTagList(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return [];
    return Array.from(el.querySelectorAll('.asf-tag-text')).map(s => s.textContent);
  }

  /** Add a value to a tag list container */
  function addTagToList(containerId) {
    const rowEl = document.querySelector(`[data-add-target="${containerId}"]`);
    const inputEl = document.querySelector(`input[data-add-target="${containerId}"]`);
    if (!inputEl) return;
    const val = inputEl.value.trim();
    if (!val) return;
    const current = readTagList(containerId);
    if (current.includes(val)) { inputEl.value = ''; return; }
    const isDeny = containerId.includes('-deny');
    renderTagList(containerId, [...current, val], isDeny);
    inputEl.value = '';
    updatePermChangedIndicator();
  }

  /** Remove a tag by index */
  function removeTag(containerId, idx) {
    const current = readTagList(containerId);
    current.splice(idx, 1);
    const isDeny = containerId.includes('-deny');
    renderTagList(containerId, current, isDeny);
    updatePermChangedIndicator();
  }

  /* ── Permissions change detection ──────────────────────────────────────── */
  function getPermissionsFromForm() {
    const perms = emptyPermissions();
    perms.mode = F.permMode() ? F.permMode().value : 'restricted';
    for (const [id, [section, key]] of Object.entries(PERM_LISTS)) {
      if (!perms[section]) perms[section] = {};
      perms[section][key] = readTagList(id);
    }
    return perms;
  }

  function permissionsChanged(oldPerms, newPerms) {
    if (!oldPerms && !newPerms) return false;
    if (!oldPerms || !newPerms) return true;
    return JSON.stringify(oldPerms) !== JSON.stringify(newPerms);
  }

  function updatePermChangedIndicator() {
    const current = getPermissionsFromForm();
    const changed = permissionsChanged(ASF.originalPerms, current);
    if (asfPermChanged)  asfPermChanged.classList.toggle('hidden', !changed);
    if (btnSettReload) {
      btnSettReload.classList.toggle('hidden', !changed);
    }
    // Update dirty dot — show if any field has changed from the original
    updateDirtyIndicator();
  }

  function updateDirtyIndicator() {
    if (!asfDirtyDot || !ASF.originalAgent) return;
    const current = collectFormData();
    const dirty = JSON.stringify(current) !== JSON.stringify(ASF.originalAgent);
    asfDirtyDot.classList.toggle('hidden', !dirty);
  }

  function updateModeBadge(mode) {
    if (!asfModeBadge) return;
    asfModeBadge.textContent = mode || '';
    asfModeBadge.className = 'asf-mode-badge asf-mode-' + (mode || 'restricted');
  }

  /* ── Populate form from agent object ────────────────────────────────────── */
  function populateForm(agent) {
    if (!agent) return;
    const set = (fn, val) => { const el = fn(); if (el) el.value = val || ''; };
    set(F.name,        agent.name);
    set(F.path,        agent.path);
    set(F.description, agent.description);
    set(F.primaryRuntime,   agent.primary_runtime || agent.runtime);
    set(F.primaryModel,     agent.primary_model || agent.model);
    set(F.fallbackRuntime,  agent.fallback_runtime);
    set(F.fallbackModel,    agent.fallback_model);
    const mcEl = F.maxConcurrent();
    if (mcEl) mcEl.value = agent.max_concurrent != null ? String(agent.max_concurrent) : '1';

    const perms = agent.permissions || emptyPermissions();
    set(F.permMode, perms.mode);
    updateModeBadge(perms.mode);

    for (const [id, [section, key]] of Object.entries(PERM_LISTS)) {
      const vals = perms[section] ? (perms[section][key] || []) : [];
      const isDeny = id.includes('-deny');
      renderTagList(id, vals, isDeny);
    }

    ASF.originalPerms = deepClone(perms);
    ASF.originalAgent = deepClone(agent);
    updatePermChangedIndicator();
    clearBanners();
  }

  /* ── Collect form data into agent object ────────────────────────────────── */
  function collectFormData() {
    const get = (fn) => { const el = fn(); return el ? el.value.trim() : ''; };
    const agent = {
      name:        get(F.name),
      path:        get(F.path),
      description: get(F.description) || undefined,
      primary_runtime:  get(F.primaryRuntime)   || undefined,
      primary_model:    get(F.primaryModel)     || undefined,
      fallback_runtime: get(F.fallbackRuntime)  || undefined,
      fallback_model:   get(F.fallbackModel)    || undefined,
      max_concurrent: (() => {
        const el = F.maxConcurrent();
        if (!el || el.value.trim() === '') return undefined;
        const v = parseInt(el.value, 10);
        return isNaN(v) ? undefined : v;
      })(),
      permissions: getPermissionsFromForm(),
    };
    // Strip undefined keys
    Object.keys(agent).forEach(k => agent[k] === undefined && delete agent[k]);
    return agent;
  }

  /* ── Validation ──────────────────────────────────────────────────────────── */
  function validate(agent) {
    const errs = [];
    if (!agent.name) errs.push('Name is required');
    else if (!/^[a-z0-9_-]+$/.test(agent.name)) errs.push('Name must be lowercase with hyphens/underscores only');
    if (!agent.path) errs.push('Working path is required');
    else if (!agent.path.startsWith('/') && !agent.path.startsWith('~')) errs.push('Working path must start with / or ~');
    if (agent.max_concurrent !== undefined && agent.max_concurrent !== null) {
      if (!Number.isInteger(agent.max_concurrent) || agent.max_concurrent < 1) {
        errs.push('Max concurrent must be an integer ≥ 1');
      }
    }
    return errs;
  }

  /* ── Populate agent selector dropdown ──────────────────────────────────── */
  function populateSelector(agents, selectName) {
    if (!asfSelector) return;
    asfSelector.innerHTML = '';
    agents.forEach(a => {
      const opt = document.createElement('option');
      opt.value = a.name;
      opt.textContent = a.name;
      if (a.name === selectName) opt.selected = true;
      asfSelector.appendChild(opt);
    });
  }

  /* ── Open settings ──────────────────────────────────────────────────────── */
  async function openSettings() {
    if (!modalSettings) return;
    clearBanners();
    modalSettings.classList.remove('hidden');
    if (btnSettSave) { btnSettSave.disabled = true; btnSettSave.textContent = 'Loading…'; }
    try {
      ASF.config = await apiRequest('GET', '/agents-config');
      const agents = ASF.config.agents || [];
      const firstName = agents.length ? agents[0].name : null;
      populateSelector(agents, firstName);
      if (firstName) {
        ASF.selectedName = firstName;
        populateForm(agents[0]);
      }
    } catch (e) {
      showErr('Failed to load agents: ' + e.message);
    } finally {
      if (btnSettSave) { btnSettSave.disabled = false; btnSettSave.textContent = '💾 Save'; }
    }
  }

  function closeSettings() {
    if (modalSettings) modalSettings.classList.add('hidden');
  }

  /* ── Save settings ──────────────────────────────────────────────────────── */
  async function saveSettings() {
    clearBanners();
    const agent = collectFormData();
    const errs = validate(agent);
    if (errs.length) { showErr(errs.join(' • ')); return; }

    if (!ASF.config) return;
    const idx = ASF.config.agents.findIndex(a => a.name === ASF.selectedName);
    const newAgents = idx >= 0
      ? ASF.config.agents.map((a, i) => i === idx ? agent : a)
      : [...ASF.config.agents, agent];
    const payload = { agents: newAgents };

    if (btnSettSave) { btnSettSave.disabled = true; btnSettSave.textContent = 'Saving…'; }
    try {
      await apiRequest('PUT', '/agents-config', payload);
      ASF.config = payload;
      ASF.selectedName = agent.name;
      ASF.originalPerms = deepClone(agent.permissions);
      ASF.originalAgent = deepClone(agent);
      populateSelector(newAgents, agent.name);
      updatePermChangedIndicator();
      updateDirtyIndicator();
      showOk('\u2713 Agent settings saved');
    } catch (e) {
      showErr('Save failed: ' + e.message);
    } finally {
      if (btnSettSave) { btnSettSave.disabled = false; btnSettSave.textContent = '💾 Save'; }
    }
  }

  /* ── Reload services ────────────────────────────────────────────────────── */
  async function reloadServices() {
    if (btnSettReload) { btnSettReload.disabled = true; btnSettReload.textContent = 'Reloading…'; }
    try {
      await apiRequest('POST', '/reload-agents');
      showOk('✓ Agents reloaded in memory');
    } catch(e) {
      showOk('⚠ Could not hot-reload — changes saved to disk (restart service to apply)');
    } finally {
      if (btnSettReload) { btnSettReload.disabled = false; btnSettReload.textContent = '🔄 Reload Services'; }
    }
  }

  /* ── Event delegation for tag lists ────────────────────────────────────── */
  if (modalSettings) {
    modalSettings.addEventListener('click', (e) => {
      // Close on overlay click
      if (e.target === modalSettings) { closeSettings(); return; }

      // Remove tag button
      if (e.target.classList.contains('asf-tag-rm')) {
        const containerId = e.target.dataset.container;
        const idx = parseInt(e.target.dataset.idx, 10);
        removeTag(containerId, idx);
        return;
      }

      // Add tag button
      if (e.target.classList.contains('asf-add-btn')) {
        addTagToList(e.target.dataset.addTarget);
        return;
      }
    });

    // Enter key in tag inputs
    modalSettings.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.classList.contains('asf-tag-input')) {
        e.preventDefault();
        addTagToList(e.target.dataset.addTarget);
      }
    });

    // Mode badge update
    const permModeEl = F.permMode();
    if (permModeEl) {
      permModeEl.addEventListener('change', () => {
        updateModeBadge(permModeEl.value);
        updatePermChangedIndicator();
      });
    }
  }


  /* ── .env File Editor ──────────────────────────────────────────────────── */
  const envEditor   = document.getElementById('asf-env-editor');
  const btnEnvLoad  = document.getElementById('btn-env-load');
  const btnEnvSave  = document.getElementById('btn-env-save');
  const btnEnvRestart = document.getElementById('btn-env-restart');
  const envStatus   = document.getElementById('asf-env-status');

  function showEnvStatus(msg, isError) {
    if (!envStatus) return;
    envStatus.textContent = msg;
    envStatus.className = 'asf-env-status' + (isError ? ' asf-env-error' : ' asf-env-ok');
    envStatus.classList.remove('hidden');
    setTimeout(() => envStatus.classList.add('hidden'), 5000);
  }

  async function loadEnvFile() {
    if (!envEditor) return;
    envEditor.value = 'Loading...';
    try {
      const data = await apiRequest('GET', '/settings/env');
      envEditor.value = data.content || '';
      if (!data.exists) showEnvStatus('No .env file found — a new one will be created on save.', false);
    } catch (e) {
      envEditor.value = '';
      showEnvStatus('Failed to load .env: ' + e.message, true);
    }
  }

  async function saveEnvFile() {
    if (!envEditor) return;
    try {
      const data = await apiRequest('PUT', '/settings/env', { content: envEditor.value });
      showEnvStatus('✓ .env saved. ' + (data.warning || ''), false);
    } catch (e) {
      showEnvStatus('Failed to save: ' + e.message, true);
    }
  }

  async function restartDevServices() {
    if (!confirm('Restart all dev services? The API will briefly disconnect.')) return;
    showEnvStatus('Restarting services...', false);
    try {
      const data = await apiRequest('POST', '/settings/restart-services');
      const summary = Object.entries(data.results || {}).map(([s, r]) => s.replace('.service', '') + ': ' + r).join('\n');
      showEnvStatus('✓ ' + summary, false);
    } catch (e) {
      showEnvStatus('Restart request sent. API may reconnect shortly.', false);
    }
  }

  if (btnEnvLoad)    btnEnvLoad.addEventListener('click', loadEnvFile);
  if (btnEnvSave)    btnEnvSave.addEventListener('click', saveEnvFile);
  if (btnEnvRestart) btnEnvRestart.addEventListener('click', restartDevServices);

  // Auto-load .env when settings panel opens
  const _origOpenSettings = typeof openSettings === 'function' ? openSettings : null;
  if (_origOpenSettings) {
    // Monkey-patch openSettings to also load .env
    const _wrappedOpen = async function() {
      await _origOpenSettings();
      loadEnvFile();
    };
    if (btnSettings) {
      btnSettings.removeEventListener('click', openSettings);
      btnSettings.addEventListener('click', _wrappedOpen);
    }
  }

  // Agent selector change
  if (asfSelector) {
    asfSelector.addEventListener('change', () => {
      const name = asfSelector.value;
      ASF.selectedName = name;
      const agent = (ASF.config?.agents || []).find(a => a.name === name);
      if (agent) populateForm(agent);
    });
  }

  if (btnSettings)   btnSettings.addEventListener('click',   openSettings);
  if (btnSettSave)   btnSettSave.addEventListener('click',   saveSettings);
  if (btnSettClose)  btnSettClose.addEventListener('click',  closeSettings);
  if (btnSettCancel) btnSettCancel.addEventListener('click', () => {
    // Discard: restore original
    if (ASF.originalAgent && ASF.config) {
      const agent = (ASF.config.agents || []).find(a => a.name === ASF.selectedName);
      if (agent) populateForm(agent);
    }
  });
  if (btnSettReload) btnSettReload.addEventListener('click', reloadServices);

  // Dirty detection on basic text fields
  if (modalSettings) {
    ['asf-name','asf-path','asf-description','asf-primary-runtime','asf-primary-model','asf-fallback-runtime','asf-fallback-model','asf-max-concurrent'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', updateDirtyIndicator);
    });
  }

  // Add new agent
  if (btnSettAddAgent) {
    btnSettAddAgent.addEventListener('click', () => {
      if (!ASF.config) return;
      const newAgent = {
        name: 'new-agent',
        path: '/opt/',
        description: '',
        permissions: emptyPermissions(),
      };
      ASF.config.agents.push(newAgent);
      populateSelector(ASF.config.agents, 'new-agent');
      ASF.selectedName = 'new-agent';
      ASF.originalAgent = null; // it's new
      populateForm(newAgent);
      const nameEl = F.name();
      if (nameEl) { nameEl.focus(); nameEl.select(); }
    });
  }

  // Delete agent
  if (btnDeleteAgent) {
    btnDeleteAgent.addEventListener('click', async () => {
      if (!ASF.config || !ASF.selectedName) return;
      const name = ASF.selectedName;
      if (!confirm(`Delete agent "${name}"? This cannot be undone.`)) return;
      const newAgents = ASF.config.agents.filter(a => a.name !== name);
      const payload = { agents: newAgents };
      try {
        await apiRequest('PUT', '/agents-config', payload);
        ASF.config = payload;
        if (newAgents.length) {
          populateSelector(newAgents, newAgents[0].name);
          ASF.selectedName = newAgents[0].name;
          populateForm(newAgents[0]);
        } else {
          if (asfSelector) asfSelector.innerHTML = '';
        }
        showOk(`✓ Agent "${name}" deleted`);
      } catch(e) {
        showErr('Delete failed: ' + e.message);
      }
    });
  }

  /* --- Logs Panel --- */
  const btnLogs      = document.getElementById('btn-logs');
  const panelLogs    = document.getElementById('panel-logs');
  const logsOutput   = document.getElementById('logs-output');
  const logsService  = document.getElementById('logs-service');
  const logsSearch   = document.getElementById('logs-search');
  const logsSince    = document.getElementById('logs-since');
  const logsLive     = document.getElementById('logs-live');
  const btnLogsRefresh = document.getElementById('btn-logs-refresh');
  const btnLogsClose = document.getElementById('btn-logs-close');

  let _logsEventSource = null;

  function classifyLine(text) {
    const lower = text.toLowerCase();
    if (lower.includes('error') || lower.includes('traceback') || lower.includes('exception') || lower.includes('critical'))
      return 'log-line-error';
    if (lower.includes('warn'))
      return 'log-line-warn';
    if (lower.includes(' info') || lower.includes('[info]'))
      return 'log-line-info';
    return '';
  }

  function appendLogLine(text) {
    if (!logsOutput) return;
    const span = document.createElement('span');
    const cls = classifyLine(text);
    if (cls) span.className = cls;
    span.textContent = text + '\n';
    logsOutput.appendChild(span);
    // Auto-scroll if near bottom
    const atBottom = logsOutput.scrollHeight - logsOutput.scrollTop - logsOutput.clientHeight < 80;
    if (atBottom) logsOutput.scrollTop = logsOutput.scrollHeight;
  }

  function stopLiveStream() {
    if (_logsEventSource) {
      _logsEventSource.close();
      _logsEventSource = null;
    }
  }

  function startLiveStream() {
    stopLiveStream();
    if (!STATE.token) return;
    const service = logsService ? logsService.value : 'agent-manager-api-dev';
    const url = `${API_BASE}/logs/stream?service=${encodeURIComponent(service)}&token=${encodeURIComponent(STATE.token)}`;
    _logsEventSource = new EventSource(url);
    _logsEventSource.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        if (data.line) appendLogLine(data.line);
      } catch (_) {
        appendLogLine(evt.data);
      }
    };
    _logsEventSource.onerror = () => {
      // EventSource auto-reconnects; if we deliberately closed, do nothing
      if (logsLive && !logsLive.checked) stopLiveStream();
    };
  }

  async function loadLogs() {
    if (!logsOutput) return;
    logsOutput.innerHTML = '';
    const service = logsService ? logsService.value : 'agent-manager-api-dev';
    const search  = logsSearch ? logsSearch.value.trim() : '';
    const since   = logsSince ? logsSince.value : '';
    const params = new URLSearchParams({ service, lines: '200' });
    if (search) params.set('search', search);
    if (since) params.set('since', new Date(since).toISOString());
    try {
      const data = await apiRequest('GET', `/logs?${params.toString()}`);
      (data.lines || []).forEach(line => appendLogLine(line));
    } catch (e) {
      appendLogLine('⚠ Error loading logs: ' + e.message);
    }
    // Start live if checkbox is on
    if (logsLive && logsLive.checked) startLiveStream();
  }

  function toggleLogs() {
    if (!panelLogs) return;
    const willShow = panelLogs.classList.contains('hidden');
    panelLogs.classList.toggle('hidden');
    if (willShow) {
      loadLogs();
    } else {
      stopLiveStream();
    }
  }

  if (btnLogs)        btnLogs.addEventListener('click', toggleLogs);
  if (btnLogsClose)   btnLogsClose.addEventListener('click', () => {
    if (panelLogs) panelLogs.classList.add('hidden');
    stopLiveStream();
    if (logsLive) logsLive.checked = false;
  });
  if (btnLogsRefresh) btnLogsRefresh.addEventListener('click', loadLogs);
  if (logsService)    logsService.addEventListener('change', loadLogs);
  if (logsLive)       logsLive.addEventListener('change', () => {
    if (logsLive.checked) startLiveStream();
    else stopLiveStream();
  });
  // Search on Enter
  if (logsSearch) {
    logsSearch.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') loadLogs();
    });
  }
})();



// ─── Skills Manager Panel ────────────────────────────────────────────────────
// Mirrors the Canvas pushover panel pattern.
// ═══════════════════════════════════════════════════════════════════════════════

let _skillsPanelOpen = false;
let _skillsCache = [];           // cached skills list from API
let _skillsDetailKey = null;     // currently viewed skill key
let _skillsAgentFilter = '';     // selected agent name, '' = all
let _skillsAgentsLoaded = false; // whether the agent dropdown is populated

// ── Panel toggle ─────────────────────────────────────────────────────────────

function toggleSkillsPanel() {
  if (_skillsPanelOpen) closeSkillsPanel();
  else openSkillsPanel();
}

function openSkillsPanel() {
  const panel = document.getElementById('skills-panel');
  if (!panel) return;
  panel.classList.remove('skills-hidden');
  panel.classList.add('skills-open');
  _skillsPanelOpen = true;
  _loadSkillsAgentList();
  loadSkillsList();
}

function closeSkillsPanel() {
  const panel = document.getElementById('skills-panel');
  if (!panel) return;
  panel.classList.add('skills-hidden');
  panel.classList.remove('skills-open');
  _skillsPanelOpen = false;
}

// ── Skills list loading ──────────────────────────────────────────────────────

async function loadSkillsList() {
  const listEl = document.getElementById('skills-list');
  if (!listEl) return;

  try {
    const agentParam = _skillsAgentFilter ? `?agent=${encodeURIComponent(_skillsAgentFilter)}` : '';
    const resp = await fetch(`${API_BASE}/skills${agentParam}`, {
      headers: { 'Authorization': `Bearer ${STATE.token}` }
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    _skillsCache = data.skills || [];
    _renderSkillsList();
  } catch (e) {
    listEl.innerHTML = `<div class="skills-empty">
      <div style="font-size:36px;opacity:0.4;">⚠️</div>
      <div style="font-size:14px;color:var(--danger);margin-top:8px;">Failed to load skills</div>
      <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">${e.message}</div>
    </div>`;
  }
}

function _renderSkillsList() {
  const listEl = document.getElementById('skills-list');
  if (!listEl) return;

  const searchVal = (document.getElementById('skills-search')?.value || '').toLowerCase();
  const filterOrigin = document.getElementById('skills-filter-origin')?.value || '';

  let filtered = _skillsCache;

  if (searchVal) {
    filtered = filtered.filter(s =>
      s.name.toLowerCase().includes(searchVal) ||
      (s.description || '').toLowerCase().includes(searchVal) ||
      (s.dir_name || '').toLowerCase().includes(searchVal) ||
      (s.source_label || '').toLowerCase().includes(searchVal)
    );
  }

  if (filterOrigin) {
    if (filterOrigin === 'unknown') {
      filtered = filtered.filter(s => !s.origin);
    } else {
      filtered = filtered.filter(s => s.origin?.origin_type === filterOrigin);
    }
  }

  if (filtered.length === 0) {
    const agentHint = _skillsAgentFilter
      ? `<div style="font-size:12px;color:var(--text-muted);margin-top:6px;">Agent <strong>${_escHtml(_skillsAgentFilter)}</strong> has no skills in .github/skills/ or .claude/skills/</div>`
      : '';
    listEl.innerHTML = `<div class="skills-empty">
      <div style="font-size:36px;opacity:0.4;">🔍</div>
      <div style="font-size:14px;color:var(--text-secondary);margin-top:8px;">No skills match</div>
      ${agentHint}
    </div>`;
    return;
  }

  listEl.innerHTML = '';
  for (const skill of filtered) {
    const card = document.createElement('div');
    card.className = 'skill-card';
    card.addEventListener('click', () => _openSkillDetail(skill.skill_key));

    const originType = skill.origin?.origin_type || 'unknown';
    const badgeClass = {
      'git_repo': 'skill-badge-git',
      'website': 'skill-badge-website',
      'local': 'skill-badge-local',
      'unknown': 'skill-badge-unknown',
    }[originType] || 'skill-badge-unknown';

    const badgeText = {
      'git_repo': '🔗 Git',
      'website': '🌐 Web',
      'local': '📁 Local',
      'unknown': '❓ No Origin',
    }[originType] || '❓ Unknown';

    const updateBadge = skill.origin?.update_available
      ? '<span class="skill-badge skill-badge-update">⬆ Update</span>'
      : '';

    const runtimes = (skill.runtimes || []).map(r => {
      const icons = { claude: '🟣', copilot: '🟢', gemini: '🔵' };
      return icons[r] || '⚪';
    }).join(' ');

    card.innerHTML = `
      <div class="skill-card-header">
        <span class="skill-card-name">${_escHtml(skill.name)}</span>
        <span class="skill-card-badges">
          ${updateBadge}
          <span class="skill-badge ${badgeClass}">${badgeText}</span>
        </span>
      </div>
      ${skill.description ? `<div class="skill-card-desc">${_escHtml(skill.description)}</div>` : ''}
      <div class="skill-card-meta">
        <span>📂 ${_escHtml(skill.source_label)}</span>
        ${skill.version ? `<span>v${_escHtml(skill.version)}</span>` : ''}
        ${runtimes ? `<span>${runtimes}</span>` : ''}
      </div>
    `;
    listEl.appendChild(card);
  }
}

function _escHtml(text) {
  const d = document.createElement('div');
  d.textContent = text || '';
  return d.innerHTML;
}

// ── Skill detail view ────────────────────────────────────────────────────────

function _openSkillDetail(skillKey) {
  _skillsDetailKey = skillKey;
  const skill = _skillsCache.find(s => s.skill_key === skillKey);
  if (!skill) return;

  const detailEl = document.getElementById('skills-detail');
  const nameEl = document.getElementById('skills-detail-name');
  const bodyEl = document.getElementById('skills-detail-body');
  if (!detailEl || !bodyEl) return;

  nameEl.textContent = skill.name;
  detailEl.classList.remove('hidden');

  const origin = skill.origin || {};
  const originType = origin.origin_type || '';
  const hasOrigin = !!originType;

  let originSection = '';
  if (hasOrigin) {
    const urlDisplay = origin.origin_url
      ? `<a href="${_escHtml(origin.origin_url)}" target="_blank">${_escHtml(origin.origin_url)}</a>`
      : '<span style="color:var(--text-muted)">—</span>';

    originSection = `
      <div class="skill-detail-section">
        <h4>Origin</h4>
        <div class="skill-detail-row"><span class="label">Type</span><span class="value">${_escHtml(originType)}</span></div>
        <div class="skill-detail-row"><span class="label">URL</span><span class="value">${urlDisplay}</span></div>
        <div class="skill-detail-row"><span class="label">Path in Repo</span><span class="value">${_escHtml(origin.origin_path || '—')}</span></div>
        ${origin.last_checked ? `<div class="skill-detail-row"><span class="label">Last Checked</span><span class="value">${_timeAgo(origin.last_checked)}</span></div>` : ''}
        ${origin.last_updated ? `<div class="skill-detail-row"><span class="label">Last Updated</span><span class="value">${_timeAgo(origin.last_updated)}</span></div>` : ''}
        ${origin.notes ? `<div class="skill-detail-row"><span class="label">Notes</span><span class="value">${_escHtml(origin.notes)}</span></div>` : ''}
        ${origin.update_available ? `<div class="skill-detail-row"><span class="label">Status</span><span class="value skill-status-success">⬆ Update available</span></div>` : ''}
      </div>
      <div class="skill-detail-section">
        <h4>Actions</h4>
        <div class="skill-origin-actions">
          <button class="btn-skill-action btn-skill-secondary" onclick="_skillCheckUpdate('${_escHtml(skillKey)}')">🔍 Check for Updates</button>
          ${originType === 'git_repo' ? `<button class="btn-skill-action btn-skill-update" onclick="_skillTriggerUpdate('${_escHtml(skillKey)}')">⬆ Update Skill</button>` : ''}
          <button class="btn-skill-action btn-skill-secondary" onclick="_showOriginForm('${_escHtml(skillKey)}')">✏️ Edit Origin</button>
        </div>
        <div id="skill-action-status" style="margin-top:10px;font-size:13px;"></div>
      </div>
    `;
  } else {
    originSection = `
      <div class="skill-detail-section">
        <h4>Origin</h4>
        <div style="color:var(--danger);font-size:13px;margin-bottom:10px;">
          ⚠️ No origin metadata recorded for this skill. Set the origin below so updates can be tracked.
        </div>
        <button class="btn-skill-action btn-skill-primary" onclick="_showOriginForm('${_escHtml(skillKey)}')">📝 Record Origin</button>
        <div id="skill-action-status" style="margin-top:10px;font-size:13px;"></div>
      </div>
    `;
  }

  bodyEl.innerHTML = `
    <div class="skill-detail-section">
      <h4>Details</h4>
      <div class="skill-detail-row"><span class="label">Name</span><span class="value">${_escHtml(skill.name)}</span></div>
      <div class="skill-detail-row"><span class="label">Key</span><span class="value" style="font-family:monospace;font-size:12px;">${_escHtml(skill.skill_key)}</span></div>
      <div class="skill-detail-row"><span class="label">Local Path</span><span class="value" style="font-family:monospace;font-size:11px;">${_escHtml(skill.path)}</span></div>
      <div class="skill-detail-row"><span class="label">Source</span><span class="value">${_escHtml(skill.source_label)}</span></div>
      ${skill.version ? `<div class="skill-detail-row"><span class="label">Version</span><span class="value">${_escHtml(skill.version)}</span></div>` : ''}
      ${skill.author ? `<div class="skill-detail-row"><span class="label">Author</span><span class="value">${_escHtml(skill.author)}</span></div>` : ''}
      ${skill.category ? `<div class="skill-detail-row"><span class="label">Category</span><span class="value">${_escHtml(skill.category)}</span></div>` : ''}
      <div class="skill-detail-row"><span class="label">Runtimes</span><span class="value">${(skill.runtimes || []).join(', ') || '—'}</span></div>
      <div class="skill-detail-row"><span class="label">Has Metadata</span><span class="value">${skill.has_metadata ? '✅' : '❌'}</span></div>
      <div class="skill-detail-row"><span class="label">Has SKILL.md</span><span class="value">${skill.has_skill_md ? '✅' : '❌'}</span></div>
      <div class="skill-detail-row"><span class="label">Checksum</span><span class="value" style="font-family:monospace;font-size:11px;">${_escHtml(skill.checksum)}</span></div>
    </div>
    ${skill.description ? `<div class="skill-detail-section"><h4>Description</h4><div style="font-size:13px;color:var(--text-secondary);line-height:1.5;">${_escHtml(skill.description)}</div></div>` : ''}
    ${originSection}
    <div id="skill-origin-form-container"></div>
    <div class="skill-detail-section skill-danger-zone">
      <h4 style="color:var(--danger, #e74c3c);">⚠️ Danger Zone</h4>
      <p style="font-size:12px;color:var(--text-muted);margin:4px 0 10px;">Permanently remove this skill from disk. This cannot be undone.</p>
      <button class="btn-skill-action btn-skill-danger" onclick="_deleteSkill('${_escHtml(skill.skill_key)}')">🗑 Delete Skill</button>
    </div>
  `;
}

function _closeSkillDetail() {
  const detailEl = document.getElementById('skills-detail');
  if (detailEl) detailEl.classList.add('hidden');
  _skillsDetailKey = null;
}

// ── Time formatting ──────────────────────────────────────────────────────────

function _timeAgo(ts) {
  if (!ts) return '—';
  const secs = Math.floor(Date.now() / 1000 - ts);
  if (secs < 60) return 'just now';
  if (secs < 3600) return `${Math.floor(secs/60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs/3600)}h ago`;
  return `${Math.floor(secs/86400)}d ago`;
}

// ── Origin form ──────────────────────────────────────────────────────────────

function _showOriginForm(skillKey) {
  const container = document.getElementById('skill-origin-form-container');
  if (!container) return;

  const skill = _skillsCache.find(s => s.skill_key === skillKey);
  const origin = skill?.origin || {};

  container.innerHTML = `
    <div class="skill-detail-section">
      <h4>${origin.origin_type ? 'Edit' : 'Record'} Origin Metadata</h4>
      <div class="skill-origin-form">
        <label>Origin Type</label>
        <select id="origin-type">
          <option value="git_repo" ${origin.origin_type === 'git_repo' ? 'selected' : ''}>Git Repository</option>
          <option value="website" ${origin.origin_type === 'website' ? 'selected' : ''}>Website (e.g. skills.sh)</option>
          <option value="local" ${origin.origin_type === 'local' ? 'selected' : ''}>Local / Self-authored</option>
          <option value="unknown" ${origin.origin_type === 'unknown' ? 'selected' : ''}>Unknown</option>
        </select>
        <label>Origin URL <span style="color:var(--text-muted);font-weight:400;">(git clone URL or website)</span></label>
        <input type="text" id="origin-url" value="${_escHtml(origin.origin_url || '')}" placeholder="https://github.com/org/repo.git" />
        <label>Path in Origin <span style="color:var(--text-muted);font-weight:400;">(folder path within the repo)</span></label>
        <input type="text" id="origin-path" value="${_escHtml(origin.origin_path || '')}" placeholder="skills/my-skill" />
        <label>Notes</label>
        <textarea id="origin-notes" placeholder="e.g. Copied from skills.sh on 2026-03-27, modified locally">${_escHtml(origin.notes || '')}</textarea>
        <div class="skill-origin-actions">
          <button class="btn-skill-action btn-skill-primary" onclick="_saveOrigin('${_escHtml(skillKey)}')">💾 Save Origin</button>
          ${origin.origin_type ? `<button class="btn-skill-action btn-skill-danger" onclick="_deleteOrigin('${_escHtml(skillKey)}')">🗑 Remove Origin</button>` : ''}
          <button class="btn-skill-action btn-skill-secondary" onclick="document.getElementById('skill-origin-form-container').innerHTML=''">Cancel</button>
        </div>
      </div>
    </div>
  `;
}

async function _saveOrigin(skillKey) {
  const originType = document.getElementById('origin-type')?.value || 'unknown';
  const originUrl = document.getElementById('origin-url')?.value?.trim() || '';
  const originPath = document.getElementById('origin-path')?.value?.trim() || '';
  const notes = document.getElementById('origin-notes')?.value?.trim() || '';

  try {
    const resp = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillKey)}/origin`, {
      method: 'PUT',
      headers: {
        'Authorization': `Bearer ${STATE.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ origin_type: originType, origin_url: originUrl, origin_path: originPath, notes }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    _showSkillToast('Origin metadata saved ✅', 'success');
    await loadSkillsList();
    _openSkillDetail(skillKey);
  } catch (e) {
    _showSkillToast(`Failed to save origin: ${e.message}`, 'error');
  }
}

async function _deleteOrigin(skillKey) {
  if (!confirm('Remove origin metadata for this skill?')) return;
  try {
    const resp = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillKey)}/origin`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${STATE.token}` },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    _showSkillToast('Origin metadata removed', 'info');
    await loadSkillsList();
    _openSkillDetail(skillKey);
  } catch (e) {
    _showSkillToast(`Failed: ${e.message}`, 'error');
  }
}

async function _deleteSkill(skillKey) {
  const skill = _skillsCache.find(s => s.skill_key === skillKey);
  const displayName = skill ? skill.name : skillKey;
  if (!confirm(`Permanently delete skill "${displayName}"?\n\nThis will remove the skill from disk. This action cannot be undone.`)) return;
  try {
    const resp = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillKey)}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${STATE.token}` },
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    _showSkillToast(data.message || 'Skill deleted', 'success');
    _closeSkillDetail();
    await loadSkillsList();
  } catch (e) {
    _showSkillToast(`Failed to delete skill: ${e.message}`, 'error');
  }
}

// ── Update check ─────────────────────────────────────────────────────────────

async function _skillCheckUpdate(skillKey) {
  const statusEl = document.getElementById('skill-action-status');
  if (statusEl) statusEl.innerHTML = '<span class="skill-status-checking">🔍 Checking for updates...</span>';

  try {
    const resp = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillKey)}/check-update`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${STATE.token}` },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const result = await resp.json();

    if (result.error) {
      if (statusEl) statusEl.innerHTML = `<span class="skill-status-error">❌ ${_escHtml(result.error)}</span>`;
      return;
    }

    if (result.available) {
      const diffList = (result.diff_files || []).slice(0, 8).map(f => `<code>${_escHtml(f)}</code>`).join('<br>');
      if (statusEl) statusEl.innerHTML = `
        <span class="skill-status-success">⬆ Updates available!</span>
        ${diffList ? `<div style="margin-top:6px;font-size:12px;color:var(--text-muted);">${diffList}</div>` : ''}
      `;
      _showSkillToast(`Updates available for ${skillKey}`, 'success');
    } else {
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--text-muted);">✅ Up to date</span>`;
    }

    // Refresh the cache
    await loadSkillsList();
    // Re-render detail if still viewing this skill
    if (_skillsDetailKey === skillKey) {
      _openSkillDetail(skillKey);
    }
  } catch (e) {
    if (statusEl) statusEl.innerHTML = `<span class="skill-status-error">❌ ${_escHtml(e.message)}</span>`;
  }
}

// ── Trigger update (via background task) ─────────────────────────────────────

async function _skillTriggerUpdate(skillKey) {
  if (!confirm(`Update skill "${skillKey}" from its origin? This will run as a background task.`)) return;

  const statusEl = document.getElementById('skill-action-status');
  if (statusEl) statusEl.innerHTML = '<span class="skill-status-updating">⬆ Dispatching update task...</span>';

  try {
    const resp = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillKey)}/update`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${STATE.token}`,
        'X-User-Identity': STATE.identity || '',
        'X-Auth-Channel': STATE.channel || 'api',
      },
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const result = await resp.json();

    if (result.task_id) {
      if (statusEl) statusEl.innerHTML = `
        <span class="skill-status-success">✅ Update task dispatched</span>
        <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">
          Task ID: <code>${_escHtml(result.task_id)}</code>
          — check ⚡ Tasks tab for progress
        </div>
      `;
      _showSkillToast(`Update task started: ${result.task_id}`, 'success');
    } else if (result.result) {
      // Synchronous fallback
      const r = result.result;
      if (r.success) {
        if (statusEl) statusEl.innerHTML = `<span class="skill-status-success">✅ ${_escHtml(r.message)}</span>`;
        _showSkillToast('Skill updated successfully!', 'success');
        await loadSkillsList();
        if (_skillsDetailKey === skillKey) _openSkillDetail(skillKey);
      } else {
        if (statusEl) statusEl.innerHTML = `<span class="skill-status-error">❌ ${_escHtml(r.message)}</span>`;
      }
    }
  } catch (e) {
    if (statusEl) statusEl.innerHTML = `<span class="skill-status-error">❌ ${_escHtml(e.message)}</span>`;
    _showSkillToast(`Update failed: ${e.message}`, 'error');
  }
}

// ── Toast notifications ──────────────────────────────────────────────────────

function _showSkillToast(msg, type) {
  const toast = document.createElement('div');
  toast.className = `skill-toast skill-toast-${type || 'info'}`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ── Agent selector for skills ────────────────────────────────────────────────

async function _loadSkillsAgentList() {
  if (_skillsAgentsLoaded) return;
  const sel = document.getElementById('skills-agent-filter');
  if (!sel) return;

  try {
    const resp = await fetch(`${API_BASE}/agents`, {
      headers: { 'Authorization': `Bearer ${STATE.token}` }
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const agents = data.agents || [];

    // Keep the first "All Skills" option, clear any previously added
    while (sel.options.length > 1) sel.remove(1);

    for (const ag of agents.sort((a, b) => a.name.localeCompare(b.name))) {
      const opt = document.createElement('option');
      opt.value = ag.name;
      opt.textContent = ag.name;
      if (ag.description) opt.title = ag.description;
      sel.appendChild(opt);
    }
    _skillsAgentsLoaded = true;
  } catch (_) {
    // Silently fail — dropdown stays with "All Skills" only
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────

function _initSkillsPanel() {
  const edgeTab = document.getElementById('skills-edge-tab');
  if (edgeTab) edgeTab.addEventListener('click', toggleSkillsPanel);

  const closeBtn = document.getElementById('btn-skills-close');
  if (closeBtn) closeBtn.addEventListener('click', closeSkillsPanel);

  const refreshBtn = document.getElementById('btn-skills-refresh');
  if (refreshBtn) refreshBtn.addEventListener('click', () => loadSkillsList());

  const backBtn = document.getElementById('btn-skills-back');
  if (backBtn) backBtn.addEventListener('click', _closeSkillDetail);

  const searchInput = document.getElementById('skills-search');
  if (searchInput) searchInput.addEventListener('input', _renderSkillsList);

  const filterSelect = document.getElementById('skills-filter-origin');
  if (filterSelect) filterSelect.addEventListener('change', _renderSkillsList);

  const agentFilter = document.getElementById('skills-agent-filter');
  if (agentFilter) {
    agentFilter.addEventListener('change', () => {
      _skillsAgentFilter = agentFilter.value;
      _closeSkillDetail();
      loadSkillsList();
    });
  }
}

window.toggleSkillsPanel = toggleSkillsPanel;

if (document.readyState !== 'loading') {
  _initSkillsPanel();
} else {
  document.addEventListener('DOMContentLoaded', _initSkillsPanel);
}

// Expose skill panel functions for inline onclick handlers
window._skillCheckUpdate = _skillCheckUpdate;
window._skillTriggerUpdate = _skillTriggerUpdate;
window._showOriginForm = _showOriginForm;
window._saveOrigin = _saveOrigin;
window._deleteSkill = _deleteSkill;
window._deleteOrigin = _deleteOrigin;
window._closeSkillDetail = _closeSkillDetail;
window._openSkillDetail = _openSkillDetail;


// ── Agents Panel ─────────────────────────────────────────────────────────────
let _agentsPanelOpen = false;
let _agentsRefreshTimer = null;
let _agentTasksExpanded = {};
window._agentTasksExpanded = _agentTasksExpanded;

function toggleAgentsPanel() {
  _agentsPanelOpen ? closeAgentsPanel() : openAgentsPanel();
}
function openAgentsPanel() {
  const p = document.getElementById('agents-panel');
  if (!p) return;
  p.classList.remove('agents-hidden');p.classList.add('agents-open');
  _agentsPanelOpen = true;
  refreshAgentsPanel();
  _agentsRefreshTimer = setInterval(refreshAgentsPanel, 3000);
}
function closeAgentsPanel() {
  const p = document.getElementById('agents-panel');
  if (!p) return;
  p.classList.remove('agents-open');p.classList.add('agents-hidden');
  _agentsPanelOpen = false;
  clearInterval(_agentsRefreshTimer);
  _agentsRefreshTimer = null;
}

// ── Service Status Panel ──────────────────────────────────────────────────────
let _svcStatusPollInterval = null;
const SVC_POLL_MS = 30000;

function _formatSvcTimestamp(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function _applySvcStatus(name, info) {
  const dot   = document.getElementById('svc-dot-' + name);
  const badge = document.getElementById('svc-badge-' + name);
  if (!dot || !badge) return;

  const status = info.status || 'unknown';
  const dotClass   = status === 'active'   ? 'dot-active'
                   : status === 'inactive' ? 'dot-inactive'
                   : status === 'failed'   ? 'dot-failed'
                   : 'dot-unknown';
  const badgeClass = status === 'active'   ? 'badge-active'
                   : status === 'inactive' ? 'badge-inactive'
                   : status === 'failed'   ? 'badge-failed'
                   : 'badge-unknown';

  dot.className   = 'service-dot ' + dotClass;
  badge.className = 'service-badge ' + badgeClass;
  badge.textContent = status;
}

async function fetchServiceStatus() {
  const refreshBtn = document.getElementById('btn-refresh-service-status');
  if (refreshBtn) {
    refreshBtn.classList.add('spinning');
    setTimeout(function() { refreshBtn.classList.remove('spinning'); }, 600);
  }

  try {
    const data = await apiRequest('GET', '/service-status');
    const services = data.services || {};
    for (const name of Object.keys(services)) {
      _applySvcStatus(name, services[name]);
    }
    const tsEl   = document.getElementById('service-status-timestamp');
    const nodeEl = document.getElementById('service-status-node');
    if (tsEl)   tsEl.textContent = 'Updated ' + _formatSvcTimestamp(data.checked_at);
    if (nodeEl) nodeEl.textContent = data.node ? 'node: ' + data.node : '';
  } catch (err) {
    const tsEl = document.getElementById('service-status-timestamp');
    if (tsEl) tsEl.textContent = 'Status unavailable';
  }
}

function _initServiceStatus() {
  const refreshBtn = document.getElementById('btn-refresh-service-status');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function() { fetchServiceStatus(); });
  }
  fetchServiceStatus();
  _svcStatusPollInterval = setInterval(fetchServiceStatus, SVC_POLL_MS);
}

document.addEventListener('DOMContentLoaded', _initServiceStatus);
