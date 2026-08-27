/**
 * API utilities for reading, writing, and testing the LLM router
 * configuration (issue #506). Backed by /api/v1/router-config,
 * /api/v1/router/test, and /api/v1/router/status.
 */

const API_BASE = '/api/v1';

export interface RouterAllowlistEntry {
  runtime: string;
  model: string;
  hint?: string;
}

export interface RouterBrain {
  runtime: string;
  model: string;
}

export interface RouterFallback {
  runtime: string;
  model: string;
}

export interface RouterStickiness {
  enabled: boolean;
  prefer_same_runtime: boolean;
  window_seconds: number;
}

export interface RouterConfig {
  enabled: boolean;
  brain: RouterBrain;
  timeout_seconds: number;
  prompt_template: string;
  allowlist: RouterAllowlistEntry[];
  fallback: RouterFallback;
  stickiness: RouterStickiness;
  cooldown_seconds: number;
}

export interface RouterConfigResponse {
  config: RouterConfig;
  enabled_effective: boolean;
  validation_errors: string[];
}

export interface RouterTestDecision {
  runtime: string;
  model: string;
  reason: string;
  source: 'router' | 'single' | 'fallback' | '';
  latency_ms: number;
}

export interface RouterTestResponse {
  decision: RouterTestDecision;
  eligible_pairs: RouterAllowlistEntry[];
  total_ms: number;
}

export interface RouterStatusResponse {
  enabled: boolean;
  brain: RouterBrain;
  brain_available: boolean;
  cooldowns: Record<string, { reason: string; seconds_remaining: number }>;
}

/** Load the current router config, plus validation warnings and whether
 * routing is effectively enabled after the WEE_ROUTER_ENABLED env override. */
export async function loadRouterConfig(): Promise<RouterConfigResponse> {
  const resp = await fetch(`${API_BASE}/router-config`, {
    headers: { 'Accept': 'application/json' },
    credentials: 'include',
  });
  if (!resp.ok) {
    throw new Error(`Failed to load router config: ${resp.status} ${resp.statusText}`);
  }
  return resp.json() as Promise<RouterConfigResponse>;
}

/** Save the router config. Throws with the server's validation message on 422. */
export async function saveRouterConfig(config: RouterConfig): Promise<{ saved: boolean; config: RouterConfig }> {
  const resp = await fetch(`${API_BASE}/router-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ config }),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new Error(detail?.detail ?? `Failed to save router config: ${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

/** Dry-run a routing decision for a prompt without mutating any session —
 * used by the "Test route" box to iterate on the prompt/allowlist. */
export async function testRouterPrompt(prompt: string): Promise<RouterTestResponse> {
  const resp = await fetch(`${API_BASE}/router/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ prompt }),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new Error(detail?.detail ?? `Router test failed: ${resp.status} ${resp.statusText}`);
  }
  return resp.json() as Promise<RouterTestResponse>;
}

/** Router health snapshot: enabled state, brain reachability, cooldowns. */
export async function loadRouterStatus(): Promise<RouterStatusResponse> {
  const resp = await fetch(`${API_BASE}/router/status`, {
    headers: { 'Accept': 'application/json' },
    credentials: 'include',
  });
  if (!resp.ok) {
    throw new Error(`Failed to load router status: ${resp.status} ${resp.statusText}`);
  }
  return resp.json() as Promise<RouterStatusResponse>;
}

/** Empty allowlist entry for the "+ Add pair" control. */
export function emptyAllowlistEntry(): RouterAllowlistEntry {
  return { runtime: '', model: '', hint: '' };
}
