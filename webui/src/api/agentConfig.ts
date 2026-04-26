/**
 * API utilities for reading and writing agent configuration.
 * Communicates with the /api/v1/agents-config endpoint backed by /opt/agents.json.
 */

import type {
  Agent,
  AgentsConfig,
  AgentPermissions,
  ValidationResult,
  ValidationError,
  PermissionsChangeDiff,
} from '../types/agents';

/** Base URL for the orchestrator API (same origin in production) */
const API_BASE = '/api/v1';

/**
 * Retrieve the current agents.json configuration from the server.
 * @returns Parsed AgentsConfig object
 * @throws Error if network request fails or response is not valid JSON
 */
export async function loadAgents(): Promise<AgentsConfig> {
  const resp = await fetch(`${API_BASE}/agents-config`, {
    headers: { 'Accept': 'application/json' },
    credentials: 'include',
  });
  if (!resp.ok) {
    throw new Error(`Failed to load agents config: ${resp.status} ${resp.statusText}`);
  }
  const data = await resp.json() as AgentsConfig;
  if (!data || !Array.isArray(data.agents)) {
    throw new Error('Invalid agents.json: missing "agents" array');
  }
  return data;
}

/**
 * Save a modified agent back to agents.json.
 * The server automatically creates a .json.bak backup before writing.
 *
 * @param updatedAgent - The full agent object with all fields
 * @param allAgents - Current full agents config (other agents are preserved)
 * @returns Server response object { status, agent_count }
 * @throws Error if validation fails or network request fails
 */
export async function saveAgentConfig(
  updatedAgent: Agent,
  allAgents: AgentsConfig,
): Promise<{ status: string; agent_count: number }> {
  const validation = validateConfig(updatedAgent);
  if (!validation.valid) {
    const messages = validation.errors.map(e => `${e.field}: ${e.message}`).join('\n');
    throw new Error(`Validation failed:\n${messages}`);
  }

  const idx = allAgents.agents.findIndex(a => a.name === updatedAgent.name);
  const newAgents: AgentsConfig = {
    agents: idx >= 0
      ? allAgents.agents.map((a, i) => (i === idx ? updatedAgent : a))
      : [...allAgents.agents, updatedAgent],
  };

  const resp = await fetch(`${API_BASE}/agents-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(newAgents),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`Save failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}

/**
 * Validate an agent configuration object.
 * Checks required fields, type correctness, and path format.
 *
 * @param agent - Agent object to validate
 * @returns ValidationResult with an errors array (empty if valid)
 */
export function validateConfig(agent: Partial<Agent>): ValidationResult {
  const errors: ValidationError[] = [];

  if (!agent.name || typeof agent.name !== 'string' || agent.name.trim() === '') {
    errors.push({ field: 'name', message: 'Name is required' });
  } else if (!/^[a-z0-9_-]+$/.test(agent.name.trim())) {
    errors.push({ field: 'name', message: 'Name must be lowercase alphanumeric with hyphens/underscores only' });
  }

  if (!agent.path || typeof agent.path !== 'string' || agent.path.trim() === '') {
    errors.push({ field: 'path', message: 'Working path is required' });
  } else if (!agent.path.startsWith('/')) {
    errors.push({ field: 'path', message: 'Working path must be an absolute path starting with /' });
  }

  // Validate primary runtime config
  if (agent.primary_runtime !== undefined && agent.primary_runtime !== null && agent.primary_runtime !== '') {
    if (typeof agent.primary_runtime !== 'string') {
      errors.push({ field: 'primary_runtime', message: 'Primary runtime must be a string' });
    }
  }

  if (agent.primary_model !== undefined && agent.primary_model !== null && agent.primary_model !== '') {
    if (typeof agent.primary_model !== 'string') {
      errors.push({ field: 'primary_model', message: 'Primary model must be a string' });
    }
  }

  // Validate fallback runtime config
  if (agent.fallback_runtime !== undefined && agent.fallback_runtime !== null && agent.fallback_runtime !== '') {
    if (typeof agent.fallback_runtime !== 'string') {
      errors.push({ field: 'fallback_runtime', message: 'Fallback runtime must be a string' });
    }
  }

  if (agent.fallback_model !== undefined && agent.fallback_model !== null && agent.fallback_model !== '') {
    if (typeof agent.fallback_model !== 'string') {
      errors.push({ field: 'fallback_model', message: 'Fallback model must be a string' });
    }
  }

  if (agent.max_concurrent !== undefined && agent.max_concurrent !== null) {
    if (typeof agent.max_concurrent !== 'number' || !Number.isInteger(agent.max_concurrent) || agent.max_concurrent < 1) {
      errors.push({ field: 'max_concurrent', message: 'Max concurrent must be an integer >= 1' });
    }
  }

  if (agent.permissions) {
    const validModes = ['elevated', 'restricted', 'sandboxed'];
    if (!validModes.includes(agent.permissions.mode)) {
      errors.push({ field: 'permissions.mode', message: `Mode must be one of: ${validModes.join(', ')}` });
    }
  }

  return { valid: errors.length === 0, errors };
}

/**
 * Deep-compare two permission blocks and return a diff.
 * Used to decide whether to show the "Reload Services" button.
 *
 * @param oldPerms - Original permissions (may be undefined for new agents)
 * @param newPerms - Updated permissions
 * @returns PermissionsChangeDiff with changed flag and human-readable list
 */
export function detectPermissionsChange(
  oldPerms: AgentPermissions | undefined,
  newPerms: AgentPermissions | undefined,
): PermissionsChangeDiff {
  if (!oldPerms && !newPerms) return { changed: false, changes: [] };
  if (!oldPerms || !newPerms) return { changed: true, changes: ['Permissions block added/removed'] };

  const changes: string[] = [];

  if (oldPerms.mode !== newPerms.mode) {
    changes.push(`Mode: ${oldPerms.mode} → ${newPerms.mode}`);
  }

  const checkList = (label: string, a: string[], b: string[]) => {
    const added   = b.filter(x => !a.includes(x));
    const removed = a.filter(x => !b.includes(x));
    if (added.length)   changes.push(`${label} +${added.length} item(s)`);
    if (removed.length) changes.push(`${label} -${removed.length} item(s)`);
  };

  checkList('directories.allow_read', oldPerms.directories.allow_read, newPerms.directories.allow_read);
  checkList('directories.allow_write', oldPerms.directories.allow_write, newPerms.directories.allow_write);
  checkList('directories.deny', oldPerms.directories.deny, newPerms.directories.deny);
  checkList('tools.allow', oldPerms.tools.allow, newPerms.tools.allow);
  checkList('tools.deny', oldPerms.tools.deny, newPerms.tools.deny);
  checkList('network.allow_urls', oldPerms.network.allow_urls, newPerms.network.allow_urls);
  checkList('network.deny_urls', oldPerms.network.deny_urls, newPerms.network.deny_urls);
  checkList('mcp.allow', oldPerms.mcp.allow, newPerms.mcp.allow);
  checkList('mcp.deny', oldPerms.mcp.deny, newPerms.mcp.deny);

  return { changed: changes.length > 0, changes };
}

/**
 * Trigger a hot-reload of the in-memory agents cache on the backend.
 * POST /api/v1/reload-agents
 * @returns Server response with status and message
 */
export async function reloadServices(): Promise<{ status: string; message: string }> {
  const resp = await fetch(`${API_BASE}/reload-agents`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`Reload failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}
