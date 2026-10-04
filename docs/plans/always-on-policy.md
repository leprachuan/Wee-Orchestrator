# Always-On policy foundation (#519, #520)

Status: policy primitives only, disabled by default and not wired into any runtime. No autonomous execution is enabled by this change.

Trusted server adapters construct canonical Actions; the model cannot self-classify an arbitrary command as a permitted structured operation. Fingerprints bind agent, operation, execution host, resource and a canonical immutable argument snapshot. Policy rules have literal agent/operation/host/resource scopes, optional file path prefixes, allow/ask/deny outcomes, creation identity and originating approval, revocation identity/time and a monotonically increasing revision. Deny wins over ask and allow; unknown and opaque commands always require approval even with an allow rule. Structured operations must be offered as dedicated tools rather than parsed from arbitrary shell strings.

PolicyStore persists versioned JSON atomically with restrictive temporary-file permissions. Missing policy defaults disabled, corrupt policy fails closed. Writes are single-API-process synchronized; process-safe storage coordination must be added before a multi-worker deployment. The JSON holds rules and audit metadata, never action arguments or credentials. Stage 2 supplies the authorized API, approval/audit journal, execution revalidation, inherited delegation scope and enabled-flag controls.

## Execution surfaces requiring enforcement before enablement

- agent_manager.SessionManager._wee_execute_tool: bash/python/search/call_agent/browser/session_shell custom tools.
- run_wee_native SDK Tool handlers and SDK-managed built-in tools: gate built-ins with supported permission hooks, or disable them for autonomous sessions if they cannot be enforced. Handler-only gating is insufficient.
- wee_runtime and standalone wee_cli execution paths: autonomous context cannot lose policy identity when crossing runtime boundaries.
- BackgroundTaskManager, scheduler/executor and agent delegation: propagate server-owned principal/responsibility and constrain delegated permissions; spawned CLI runtimes need enforceable adapters or must remain unavailable for autonomous execution.
- Direct file/service/message/release integrations: use typed operations and gate at the actual side-effect boundary. Browser actions are opaque until a trusted operation adapter exists.

File prefix matching here is lexical: adapters must securely resolve symlinks on the execution host and avoid time-of-check/time-of-use path escapes. Command classification, identity authorization, budget constraints, external audit events, approval expiration and request fanout are not yet implemented. No universal exactly-once guarantee is possible for external actions; uncertain results require reconciliation before retry.
