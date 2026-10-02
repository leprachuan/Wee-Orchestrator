/**
 * RouterSettingsPanel — React/TypeScript component for configuring the LLM
 * model router (issue #506).
 *
 * This is the reference/future-build implementation, following the same
 * convention as AgentSettingsPanel.tsx in this directory: the live WebUI is
 * currently vanilla JS (webui/dist/app.js). Wiring an equivalent panel into
 * that bundle is tracked as a follow-up.
 *
 * Usage:
 *   import { RouterSettingsPanel } from './components/RouterSettingsPanel';
 *   <RouterSettingsPanel onClose={() => setOpen(false)} />
 */

import React, { useState, useEffect, useCallback } from 'react';
import type { RouterConfig, RouterAllowlistEntry, RouterTestResponse } from '../api/routerConfig';
import { loadRouterConfig, saveRouterConfig, testRouterPrompt, emptyAllowlistEntry } from '../api/routerConfig';
import '../styles/RouterSettingsPanel.css';

// ─── Sub-components ──────────────────────────────────────────────────────────

interface AllowlistRowProps {
  entry: RouterAllowlistEntry;
  onChange: (entry: RouterAllowlistEntry) => void;
  onRemove: () => void;
}

function AllowlistRow({ entry, onChange, onRemove }: AllowlistRowProps) {
  return (
    <div className="rsp-allowlist-row">
      <input
        type="text" className="glass-input rsp-allowlist-input" placeholder="runtime (e.g. claude-sdk)"
        value={entry.runtime} onChange={e => onChange({ ...entry, runtime: e.target.value })}
      />
      <input
        type="text" className="glass-input rsp-allowlist-input" placeholder="model (e.g. claude-opus-4.6)"
        value={entry.model} onChange={e => onChange({ ...entry, model: e.target.value })}
      />
      <input
        type="text" className="glass-input rsp-allowlist-input rsp-allowlist-hint" placeholder="hint — when to pick this pair"
        value={entry.hint ?? ''} onChange={e => onChange({ ...entry, hint: e.target.value })}
      />
      <button type="button" className="btn btn-xs btn-danger-ghost" onClick={onRemove} aria-label="Remove pair">×</button>
    </div>
  );
}

// ─── Toast ────────────────────────────────────────────────────────────────────

interface ToastProps {
  message: string;
  type: 'success' | 'error' | 'warning';
  onDismiss: () => void;
}

function Toast({ message, type, onDismiss }: ToastProps) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return (
    <div className={`rsp-toast rsp-toast--${type}`} role="alert">
      <span>{message}</span>
      <button type="button" className="toast-dismiss" onClick={onDismiss}>×</button>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

interface RouterSettingsPanelProps {
  onClose: () => void;
}

function deepClone<T>(obj: T): T {
  return JSON.parse(JSON.stringify(obj));
}

export function RouterSettingsPanel({ onClose }: RouterSettingsPanelProps) {
  const [draft, setDraft] = useState<RouterConfig | null>(null);
  const [enabledEffective, setEnabledEffective] = useState(false);
  const [serverValidationErrors, setServerValidationErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'warning' } | null>(null);

  const [testPrompt, setTestPrompt] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<RouterTestResponse | null>(null);

  // ── Load on mount ───────────────────────────────────────────────────────────
  useEffect(() => {
    loadRouterConfig()
      .then(resp => {
        setDraft(deepClone(resp.config));
        setEnabledEffective(resp.enabled_effective);
        setServerValidationErrors(resp.validation_errors);
      })
      .catch(e => setToast({ message: (e as Error).message, type: 'error' }))
      .finally(() => setLoading(false));
  }, []);

  const setField = <K extends keyof RouterConfig>(key: K, value: RouterConfig[K]) => {
    setDraft(prev => prev ? { ...prev, [key]: value } : null);
  };

  const setAllowlistEntry = (idx: number, entry: RouterAllowlistEntry) => {
    setDraft(prev => {
      if (!prev) return null;
      const allowlist = prev.allowlist.map((e, i) => (i === idx ? entry : e));
      return { ...prev, allowlist };
    });
  };

  const addAllowlistEntry = () => {
    setDraft(prev => prev ? { ...prev, allowlist: [...prev.allowlist, emptyAllowlistEntry()] } : null);
  };

  const removeAllowlistEntry = (idx: number) => {
    setDraft(prev => prev ? { ...prev, allowlist: prev.allowlist.filter((_, i) => i !== idx) } : null);
  };

  // ── Save ────────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const resp = await saveRouterConfig(draft);
      setDraft(deepClone(resp.config));
      setServerValidationErrors([]);
      setToast({ message: 'Router config saved.', type: 'success' });
    } catch (e: unknown) {
      setToast({ message: (e as Error).message, type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  // ── Test route ──────────────────────────────────────────────────────────────
  const handleTest = useCallback(async () => {
    if (!testPrompt.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const resp = await testRouterPrompt(testPrompt);
      setTestResult(resp);
    } catch (e: unknown) {
      setToast({ message: (e as Error).message, type: 'error' });
    } finally {
      setTesting(false);
    }
  }, [testPrompt]);

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="rsp-overlay" role="dialog" aria-modal="true" aria-label="LLM Router Settings">
      <div className="rsp-panel glass-panel">

        <div className="rsp-header">
          <h3 className="rsp-title">🧭 LLM Router</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="rsp-body">
          {loading && <div className="rsp-loading">Loading router config…</div>}

          {serverValidationErrors.length > 0 && (
            <div className="rsp-error-banner">
              <strong>Current config has issues:</strong>
              {serverValidationErrors.map((e, i) => <div key={i}>{e}</div>)}
            </div>
          )}

          {draft && (
            <>
              <section className="rsp-section">
                <div className="rsp-toggle-row">
                  <label className="rsp-toggle-label">
                    <input
                      type="checkbox"
                      checked={draft.enabled}
                      onChange={e => setField('enabled', e.target.checked)}
                    />
                    <span>Enable routing</span>
                  </label>
                  <span className={`rsp-badge ${enabledEffective ? 'rsp-badge--on' : 'rsp-badge--off'}`}>
                    {enabledEffective ? 'Active' : 'Inactive'}
                    {!draft.enabled && enabledEffective ? ' (env override)' : ''}
                  </span>
                </div>
                <p className="rsp-hint">
                  When enabled, sessions with runtime <code>router</code> pick their
                  target runtime/model per message. Switch a session away with
                  <code>/runtime &lt;name&gt;</code>, and back with <code>/runtime router</code>.
                </p>
              </section>

              <section className="rsp-section">
                <h4 className="rsp-section-title">Brain (decision-making LLM)</h4>
                <div className="rsp-grid-2">
                  <div className="form-group">
                    <label htmlFor="rsp-brain-runtime">Runtime</label>
                    <input
                      id="rsp-brain-runtime" type="text" className="glass-input"
                      value={draft.brain.runtime}
                      onChange={e => setField('brain', { ...draft.brain, runtime: e.target.value })}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="rsp-brain-model">Model</label>
                    <input
                      id="rsp-brain-model" type="text" className="glass-input"
                      value={draft.brain.model}
                      onChange={e => setField('brain', { ...draft.brain, model: e.target.value })}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="rsp-timeout">Timeout (seconds)</label>
                    <input
                      id="rsp-timeout" type="number" min={1} className="glass-input"
                      value={draft.timeout_seconds}
                      onChange={e => setField('timeout_seconds', Number(e.target.value))}
                    />
                  </div>
                </div>
              </section>

              <section className="rsp-section">
                <h4 className="rsp-section-title">Allowlist — candidate runtime/model pairs</h4>
                <div className="rsp-allowlist">
                  {draft.allowlist.map((entry, i) => (
                    <AllowlistRow
                      key={i} entry={entry}
                      onChange={e => setAllowlistEntry(i, e)}
                      onRemove={() => removeAllowlistEntry(i)}
                    />
                  ))}
                </div>
                <button type="button" className="btn btn-xs btn-ghost" onClick={addAllowlistEntry}>+ Add pair</button>
              </section>

              <section className="rsp-section">
                <h4 className="rsp-section-title">Fallback pair (used on brain failure / invalid decision)</h4>
                <div className="rsp-grid-2">
                  <div className="form-group">
                    <label htmlFor="rsp-fallback-runtime">Runtime</label>
                    <input
                      id="rsp-fallback-runtime" type="text" className="glass-input"
                      value={draft.fallback.runtime}
                      onChange={e => setField('fallback', { ...draft.fallback, runtime: e.target.value })}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="rsp-fallback-model">Model</label>
                    <input
                      id="rsp-fallback-model" type="text" className="glass-input"
                      value={draft.fallback.model}
                      onChange={e => setField('fallback', { ...draft.fallback, model: e.target.value })}
                    />
                  </div>
                </div>
              </section>

              <section className="rsp-section">
                <h4 className="rsp-section-title">Stickiness &amp; cooldown</h4>
                <div className="rsp-grid-2">
                  <label className="rsp-toggle-label">
                    <input
                      type="checkbox"
                      checked={draft.stickiness.enabled}
                      onChange={e => setField('stickiness', { ...draft.stickiness, enabled: e.target.checked })}
                    />
                    <span>Prefer reusing the last-routed pair</span>
                  </label>
                  <div className="form-group">
                    <label htmlFor="rsp-window">Stickiness window (seconds)</label>
                    <input
                      id="rsp-window" type="number" min={0} className="glass-input"
                      value={draft.stickiness.window_seconds}
                      onChange={e => setField('stickiness', { ...draft.stickiness, window_seconds: Number(e.target.value) })}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="rsp-cooldown">Cooldown after infra failure (seconds)</label>
                    <input
                      id="rsp-cooldown" type="number" min={0} className="glass-input"
                      value={draft.cooldown_seconds}
                      onChange={e => setField('cooldown_seconds', Number(e.target.value))}
                    />
                  </div>
                </div>
              </section>

              <section className="rsp-section">
                <h4 className="rsp-section-title">Routing prompt</h4>
                <p className="rsp-hint">
                  Must include <code>{'{allowlist_table}'}</code> and <code>{'{user_message}'}</code>;
                  <code>{'{stickiness_hint}'}</code> is optional.
                </p>
                <textarea
                  className="glass-input rsp-prompt-input"
                  rows={8}
                  value={draft.prompt_template}
                  onChange={e => setField('prompt_template', e.target.value)}
                />
              </section>

              <section className="rsp-section">
                <h4 className="rsp-section-title">Test route</h4>
                <div className="rsp-test-row">
                  <input
                    type="text" className="glass-input rsp-test-input"
                    placeholder="Try a prompt, e.g. 'write me a recursive quicksort in Python'"
                    value={testPrompt}
                    onChange={e => setTestPrompt(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleTest(); }}
                  />
                  <button type="button" className="btn btn-sm btn-ghost" onClick={handleTest} disabled={testing}>
                    {testing ? 'Routing…' : 'Test'}
                  </button>
                </div>
                {testResult && (
                  <div className="rsp-test-result">
                    <div>
                      <strong>{testResult.decision.runtime}/{testResult.decision.model}</strong>
                      {' '}<span className="rsp-test-source">({testResult.decision.source})</span>
                    </div>
                    <div className="rsp-test-reason">{testResult.decision.reason}</div>
                    <div className="rsp-test-latency">{testResult.decision.latency_ms} ms · {testResult.eligible_pairs.length} eligible</div>
                  </div>
                )}
              </section>
            </>
          )}
        </div>

        <div className="rsp-footer">
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving || !draft}>
            {saving ? 'Saving…' : '💾 Save'}
          </button>
        </div>
      </div>

      {toast && <Toast message={toast.message} type={toast.type} onDismiss={() => setToast(null)} />}
    </div>
  );
}
