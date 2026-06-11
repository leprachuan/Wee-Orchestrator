/**
 * Type definitions for the Wee-Orchestrator agent configuration system.
 * These mirror the schema of /opt/agents.json exactly.
 */

/** Allowed permission modes for an agent */
export type PermissionMode = 'elevated' | 'restricted' | 'sandboxed';

/** Directory access control lists */
export interface AgentPermissionsDirectories {
  /** Paths the agent may read */
  allow_read: string[];
  /** Paths the agent may write */
  allow_write: string[];
  /** Paths that are explicitly denied (takes precedence over allow) */
  deny: string[];
}

/** Tool access control lists (e.g. "Bash(rm -rf /)" format) */
export interface AgentPermissionsTools {
  /** Allowed tools — use ["*"] to allow all */
  allow: string[];
  /** Explicitly denied tools */
  deny: string[];
}

/** Network URL access control lists */
export interface AgentPermissionsNetwork {
  /** Allowed URL patterns — use ["*"] to allow all */
  allow_urls: string[];
  /** Denied URL patterns — use ["*"] to deny all */
  deny_urls: string[];
}

/** MCP (Model Context Protocol) server access control */
export interface AgentPermissionsMCP {
  /** Allowed MCP servers — use ["*"] to allow all */
  allow: string[];
  /** Denied MCP servers — use ["*"] to deny all */
  deny: string[];
}

/** Per-agent runtime/model overrides */
export interface AgentRuntimeOverride {
  runtime?: string;
  model?: string;
  [key: string]: unknown;
}

/** Full permissions block for an agent */
export interface AgentPermissions {
  /** Security mode governing overall agent access level */
  mode: PermissionMode;
  directories: AgentPermissionsDirectories;
  tools: AgentPermissionsTools;
  network: AgentPermissionsNetwork;
  mcp: AgentPermissionsMCP;
  /** Optional per-runtime model overrides */
  runtime_overrides?: Record<string, AgentRuntimeOverride>;
}

/** Primary and fallback runtime configuration for an agent */
export interface AgentRuntimeConfig {
  /** Primary runtime backend (copilot | claude | gemini | opencode | codex | cursor) */
  primary_runtime?: string;
  /** Primary AI model identifier */
  primary_model?: string;
  /** Fallback runtime (used if primary fails with rate limits, quota, or infrastructure errors) */
  fallback_runtime?: string;
  /** Fallback AI model identifier */
  fallback_model?: string;
}


/** Per-channel bot configuration (stored in agents.json under agent.bots.<channel>) */
export interface AgentBotChannelConfig {
  /** Secret name in the keyring — the actual token is never stored in agents.json */
  token_secret?: string;
  /** Optional list of user IDs allowed to interact with this bot */
  allowed_users?: string[];
}

/** Optional mobile bot configuration block for an agent */
export interface AgentBotsConfig {
  telegram?: AgentBotChannelConfig;
  webex?: AgentBotChannelConfig;
}

/** Bot token status returned by GET /api/v1/agents/{name}/bots/{channel}/token-status */
export interface BotTokenStatus {
  agent: string;
  channel: string;
  configured: boolean;
  /** Secret name (only present if configured) */
  secret_name?: string | null;
  allowed_users: string[];
}

/** A single agent configuration entry */
export interface Agent extends AgentRuntimeConfig {
  /** Unique identifier used in /agent set <name> */
  name: string;
  /** Human-readable description shown in agent list */
  description?: string;
  /** Working directory for agent execution */
  path: string;
  /** Maximum concurrent background tasks for this agent (>= 1) */
  max_concurrent?: number;
  /** Permission configuration for this agent */
  permissions?: AgentPermissions;
  /** Optional mobile bot configuration (Telegram/WebEx per-agent bots) */
  bots?: AgentBotsConfig;

  /** DEPRECATED: Use primary_runtime instead. Kept for backward compatibility. */
  runtime?: string;
  /** DEPRECATED: Use primary_model instead. Kept for backward compatibility. */
  model?: string;
}

/** Root structure of agents.json */
export interface AgentsConfig {
  agents: Agent[];
}

/** A field-level validation error */
export interface ValidationError {
  field: string;
  message: string;
}

/** Result of a configuration validation pass */
export interface ValidationResult {
  valid: boolean;
  errors: ValidationError[];
}

/** Diff result when comparing two permission blocks */
export interface PermissionsChangeDiff {
  changed: boolean;
  /** Human-readable list of what changed */
  changes: string[];
}
