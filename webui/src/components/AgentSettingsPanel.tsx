/**
 * AgentSettingsPanel — React/TypeScript component for editing agent configuration.
 *
 * This is the reference/future-build implementation.
 * For the live vanilla JS implementation integrated into the WebUI,
 * see the `initAgentSettingsPanel()` function in webui/dist/app.js.
 *
 * Usage:
 *   import { AgentSettingsPanel } from './components/AgentSettingsPanel';
 *   <AgentSettingsPanel onClose={() => setOpen(false)} />
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import type { Agent, AgentsConfig, AgentPermissions } from '../types/agents';
import { loadAgents, saveAgentConfig, validateConfig, detectPermissionsChange } from '../api/agentConfig';
import '../styles/AgentSettingsPanel.css';

// ─── Sub-components ──────────────────────────────────────────────────────────

interface PermListEditorProps {
  /** Label shown above the list */
  label: string;
  /** Current items in the list */
  values: string[];
  /** Called when the list changes */
  onChange: (values: string[]) => void;
  /** If true, add button renders in danger color */
  isDanger?: boolean;
  /** Placeholder text for new item input */
  placeholder?: string;
}

/** Editable list of strings with add/remove controls */
function PermListEditor({ label, values, onChange, isDanger, placeholder }: PermListEditorProps) {
  const [draft, setDraft] = useState('');

  const add = () => {
    const trimmed = draft.trim();
    if (!trimmed || values.includes(trimmed)) return;
    onChange([...values, trimmed]);
    setDraft('');
  };

  const remove = (idx: number) => onChange(values.filter((_, i) => i !== idx));

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') { e.preventDefault(); add(); }
  };

  return (
    <div className="perm-list-group">
      <label className="perm-list-label">{label}</label>
      <div className="perm-list-items">
        {values.map((v, i) => (
          <div key={i} className={`perm-list-tag ${isDanger ? 'perm-list-tag--deny' : ''}`}>
            <span className="perm-tag-text">{v}</span>
            <button
              type="button"
              className="perm-tag-remove"
              onClick={() => remove(i)}
              aria-label={`Remove ${v}`}
            >×</button>
          </div>
        ))}
      </div>
      <div className="perm-list-add-row">
        <input
          type="text"
          className="glass-input perm-list-input"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder ?? 'Add entry…'}
        />
        <button
          type="button"
          className={`btn btn-xs ${isDanger ? 'btn-danger-ghost' : 'btn-ghost'}`}
          onClick={add}
        >+ Add</button>
      </div>
    </div>
  );
}

// ─── Toast / notification ─────────────────────────────────────────────────────

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
    <div className={`agent-toast agent-toast--${type}`} role="alert">
      <span>{message}</span>
      <button type="button" className="toast-dismiss" onClick={onDismiss}>×</button>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

interface AgentSettingsPanelProps {
  /** Called when the panel should close */
  onClose: () => void;
  /** Optional auth token passed as Authorization header */
  authToken?: string;
}

/** Default empty permissions block for new agents */
function emptyPermissions(): AgentPermissions {
  return {
    mode: 'restricted',
    directories: { allow_read: [], allow_write: [], deny: [] },
    tools: { allow: ['*'], deny: [] },
    network: { allow_urls: ['*'], deny_urls: [] },
    mcp: { allow: ['*'], deny: [] },
  };
}

/** Deep clone an object via JSON serialisation */
function deepClone<T>(obj: T): T {
  return JSON.parse(JSON.stringify(obj));
}

export function AgentSettingsPanel({ onClose }: AgentSettingsPanelProps) {
  const [config, setConfig] = useState<AgentsConfig | null>(null);
  const [selectedName, setSelectedName] = useState<string>('');
  const [draft, setDraft] = useState<Agent | null>(null);
  const [originalPerms, setOriginalPerms] = useState<AgentPermissions | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'warning' } | null>(null);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);

  const permsDiff = draft && originalPerms !== undefined
    ? detectPermissionsChange(originalPerms, draft.permissions)
    : { changed: false, changes: [] };

  // ── Load agents on mount ────────────────────────────────────────────────────
  useEffect(() => {
    loadAgents()
      .then(data => {
        setConfig(data);
        if (data.agents.length > 0) {
          selectAgentByName(data.agents[0].name, data);
        }
      })
      .catch(e => setToast({ message: e.message, type: 'error' }))
      .finally(() => setLoading(false));
  }, []);

  const selectAgentByName = useCallback((name: string, cfg?: AgentsConfig) => {
    const source = cfg ?? config;
    if (!source) return;
    const agent = source.agents.find(a => a.name === name);
    if (!agent) return;
    const cloned = deepClone(agent);
    if (!cloned.permissions) cloned.permissions = emptyPermissions();
    setSelectedName(name);
    setDraft(cloned);
    setOriginalPerms(deepClone(agent.permissions));
    setValidationErrors([]);
  }, [config]);

  // ── Field helpers ───────────────────────────────────────────────────────────
  const setField = <K extends keyof Agent>(key: K, value: Agent[K]) => {
    setDraft(prev => prev ? { ...prev, [key]: value } : null);
  };

  const setPermField = <K extends keyof AgentPermissions>(key: K, value: AgentPermissions[K]) => {
    setDraft(prev => {
      if (!prev) return null;
      const perms: AgentPermissions = { ...(prev.permissions ?? emptyPermissions()), [key]: value };
      return { ...prev, permissions: perms };
    });
  };

  const setPermSubField = <
    S extends 'directories' | 'tools' | 'network' | 'mcp',
    K extends keyof AgentPermissions[S]
  >(section: S, key: K, value: AgentPermissions[S][K]) => {
    setDraft(prev => {
      if (!prev) return null;
      const perms = prev.permissions ?? emptyPermissions();
      return {
        ...prev,
        permissions: {
          ...perms,
          [section]: { ...perms[section], [key]: value },
        },
      };
    });
  };

  // ── Save handler ────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!draft || !config) return;
    const result = validateConfig(draft);
    if (!result.valid) {
      setValidationErrors(result.errors.map(e => `${e.field}: ${e.message}`));
      return;
    }
    setValidationErrors([]);
    setSaving(true);
    try {
      await saveAgentConfig(draft, config);
      const updated = await loadAgents();
      setConfig(updated);
      setOriginalPerms(deepClone(draft.permissions));
      setToast({ message: 'Agent settings saved successfully.', type: 'success' });
    } catch (e: unknown) {
      setToast({ message: (e as Error).message, type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  // ── Reload services ─────────────────────────────────────────────────────────
  const handleReloadServices = async () => {
    try {
      await fetch('/api/v1/reload-agents', { method: 'POST', credentials: 'include' });
      setToast({ message: 'Service reload requested.', type: 'success' });
    } catch {
      setToast({ message: 'Failed to trigger service reload. Restart manually.', type: 'warning' });
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────────
  const perms = draft?.permissions ?? emptyPermissions();

  return (
    <div className="asp-overlay" role="dialog" aria-modal="true" aria-label="Agent Settings">
      <div className="asp-panel glass-panel">

        {/* Header */}
        <div className="asp-header">
          <div className="asp-header-left">
            <h3 className="asp-title">⚙️ Agent Settings</h3>
            <select
              className="glass-input glass-select asp-selector"
              value={selectedName}
              onChange={e => selectAgentByName(e.target.value)}
              disabled={loading || saving}
            >
              {config?.agents.map(a => (
                <option key={a.name} value={a.name}>{a.name}</option>
              ))}
            </select>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* Body */}
        <div className="asp-body">
          {loading && <div className="asp-loading">Loading agents…</div>}

          {/* Validation errors */}
          {validationErrors.length > 0 && (
            <div className="asp-error-banner">
              {validationErrors.map((e, i) => <div key={i}>{e}</div>)}
            </div>
          )}

          {draft && (
            <>
              {/* Basic Info */}
              <section className="asp-section">
                <h4 className="asp-section-title">Basic Info</h4>
                <div className="asp-grid">
                  <div className="form-group">
                    <label htmlFor="asp-name">Name *</label>
                    <input
                      id="asp-name"
                      type="text"
                      className="glass-input"
                      value={draft.name}
                      onChange={e => setField('name', e.target.value)}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="asp-path">Working Path *</label>
                    <input
                      id="asp-path"
                      type="text"
                      className="glass-input"
                      value={draft.path}
                      onChange={e => setField('path', e.target.value)}
                    />
                  </div>
                  <div className="form-group form-group-full">
                    <label htmlFor="asp-description">Description</label>
                    <textarea
                      id="asp-description"
                      className="glass-input asp-desc-input"
                      rows={2}
                      value={draft.description ?? ''}
                      onChange={e => setField('description', e.target.value)}
                    />
                  </div>
                </div>
              </section>

              {/* Runtime Config */}
              <section className="asp-section">
                <h4 className="asp-section-title">Runtime Config</h4>
                <div className="asp-grid">
                  <div className="form-group">
                    <label htmlFor="asp-runtime">Runtime</label>
                    <select
                      id="asp-runtime"
                      className="glass-input glass-select"
                      value={draft.runtime ?? ''}
                      onChange={e => setField('runtime', e.target.value || undefined)}
                    >
                      <option value="">Default</option>
                      {['copilot', 'claude', 'gemini', 'opencode', 'codex', 'cursor'].map(r => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                  </div>
                  <div className="form-group">
                    <label htmlFor="asp-model">Model</label>
                    <input
                      id="asp-model"
                      type="text"
                      className="glass-input"
                      placeholder="e.g. claude-sonnet-4.6"
                      value={draft.model ?? ''}
                      onChange={e => setField('model', e.target.value || undefined)}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="asp-max-concurrent">Max Concurrent Tasks</label>
                    <input
                      id="asp-max-concurrent"
                      type="number"
                      className="glass-input"
                      min={1}
                      step={1}
                      placeholder="1"
                      value={draft.max_concurrent ?? 1}
                      onChange={e => {
                        const v = parseInt(e.target.value, 10);
                        setField('max_concurrent', Number.isNaN(v) ? undefined : v);
                      }}
                    />
                  </div>
                </div>
              </section>

              {/* Permissions */}
              <section className="asp-section">
                <h4 className="asp-section-title">
                  Permissions
                  <span className={`asp-mode-badge asp-mode--${perms.mode}`}>{perms.mode}</span>
                  {permsDiff.changed && (
                    <span className="asp-perm-changed-badge" title={permsDiff.changes.join('\n')}>
                      ⚠ changed
                    </span>
                  )}
                </h4>

                <div className="form-group" style={{ marginBottom: '16px' }}>
                  <label htmlFor="asp-perm-mode">Mode</label>
                  <select
                    id="asp-perm-mode"
                    className="glass-input glass-select"
                    value={perms.mode}
                    onChange={e => setPermField('mode', e.target.value as AgentPermissions['mode'])}
                  >
                    <option value="elevated">elevated</option>
                    <option value="restricted">restricted</option>
                    <option value="sandboxed">sandboxed</option>
                  </select>
                </div>

                {/* Directories */}
                <details className="asp-details" open>
                  <summary className="asp-details-title">📁 Directories</summary>
                  <div className="asp-details-body">
                    <PermListEditor label="Allow Read" values={perms.directories.allow_read}
                      onChange={v => setPermSubField('directories', 'allow_read', v)}
                      placeholder="/opt/my-agent" />
                    <PermListEditor label="Allow Write" values={perms.directories.allow_write}
                      onChange={v => setPermSubField('directories', 'allow_write', v)}
                      placeholder="/opt/my-agent" />
                    <PermListEditor label="Deny" values={perms.directories.deny} isDanger
                      onChange={v => setPermSubField('directories', 'deny', v)}
                      placeholder="/root/.ssh" />
                  </div>
                </details>

                {/* Tools */}
                <details className="asp-details">
                  <summary className="asp-details-title">🔧 Tools</summary>
                  <div className="asp-details-body">
                    <PermListEditor label="Allow" values={perms.tools.allow}
                      onChange={v => setPermSubField('tools', 'allow', v)}
                      placeholder="* or Bash(grep:*)" />
                    <PermListEditor label="Deny" values={perms.tools.deny} isDanger
                      onChange={v => setPermSubField('tools', 'deny', v)}
                      placeholder="Bash(rm -rf /)" />
                  </div>
                </details>

                {/* Network */}
                <details className="asp-details">
                  <summary className="asp-details-title">🌐 Network</summary>
                  <div className="asp-details-body">
                    <PermListEditor label="Allow URLs" values={perms.network.allow_urls}
                      onChange={v => setPermSubField('network', 'allow_urls', v)}
                      placeholder="* or github.com" />
                    <PermListEditor label="Deny URLs" values={perms.network.deny_urls} isDanger
                      onChange={v => setPermSubField('network', 'deny_urls', v)}
                      placeholder="* to block all" />
                  </div>
                </details>

                {/* MCP */}
                <details className="asp-details">
                  <summary className="asp-details-title">🔌 MCP</summary>
                  <div className="asp-details-body">
                    <PermListEditor label="Allow" values={perms.mcp.allow}
                      onChange={v => setPermSubField('mcp', 'allow', v)}
                      placeholder="* or github-mcp-server" />
                    <PermListEditor label="Deny" values={perms.mcp.deny} isDanger
                      onChange={v => setPermSubField('mcp', 'deny', v)}
                      placeholder="* to block all" />
                  </div>
                </details>
              </section>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="asp-footer">
          <div className="asp-footer-left">
            {permsDiff.changed && (
              <button
                type="button"
                className="btn btn-warning btn-sm"
                onClick={handleReloadServices}
                title={`Permissions changed:\n${permsDiff.changes.join('\n')}`}
              >
                🔄 Reload Services
              </button>
            )}
          </div>
          <div className="asp-footer-right">
            <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleSave}
              disabled={saving || loading}
            >
              {saving ? 'Saving…' : '💾 Save'}
            </button>
          </div>
        </div>

        {/* Toast */}
        {toast && (
          <Toast
            message={toast.message}
            type={toast.type}
            onDismiss={() => setToast(null)}
          />
        )}
      </div>
    </div>
  );
}

export default AgentSettingsPanel;
