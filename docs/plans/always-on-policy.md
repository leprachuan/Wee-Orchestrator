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

## Stage 2 storage slice (#522)

ApprovalStore is an isolated SQLite backend. Requests bind an owner, responsibility, idempotency intent and immutable action fingerprint. Retries with the same intent cannot create new requests; changing action or responsibility under that intent fails. Decisions (approve once, reject, revise), cancellation, expiration, and single-use execution reservations are serialized with SQLite transactions across connections. Requests and monotonically sequenced owner-filtered audit events survive restarts. Raw action arguments, secrets and tool results are not stored or broadcast. A private database file is required.

A claimed request cannot be automatically replayed after a restart. Claiming is only a reservation; it does not prove a side effect happened and does not replace current policy authorization. A real executor must revalidate identity, policy, scope and request state at the side-effect boundary, record completion/uncertain outcomes, and reconcile an ambiguous external result before any retry.

Next bounded slice: authenticated owner/approver mapping, request lists and sanitized immutable review previews, reconnectable events, bounded always-allow rule transactions and process-safe coordination with JSON policies. Always-allow is explicitly rejected by the current storage interface until that transaction exists. A transactional outbox/recovery protocol is needed across SQLite and JSON; do not treat separate DB and JSON writes as atomic. Expiry currently advances when a request is accessed; the API/coordinator must sweep and notify waiting requests. Retention controls and schema migration paths are required before deployment. No public endpoints, live UI delivery or runtime wiring are enabled yet.

## Shared approval contract (#522)
`/api/v1/autonomy/approvals`, `/approvals/{id}`, `/approvals/{id}/decision`,
`/events?after=N`, and `/rules` require the existing API bearer authentication.
All validated paired/session and shared-key clients belong to this API account;
header-supplied identities cannot grant authority or forge the audited actor.
Events support durable cursor replay; clients refresh authoritative requests on
reconnect. Clients must submit the immutable fingerprint they reviewed.

Always-allow is an exact agent/operation/host/resource grant. Opaque/unknown
operations cannot get permanent grants. A SQLite outbox records the winning
intent; JSON publication uses private process-safe locks and idempotent IDs.
Execution remains blocked while publication is incomplete. Reconciliation never
reactivates a revoked grant. Rule management supports bounded explicit grants
and revocation. Runtime state defaults to ~/.local/state/wee/autonomy, or the
private WEE_ALWAYS_ON_STATE_DIR; no credentials belong in these files.

The sole Always-On execution boundary revalidates policy and reserves every
intent before calling a trusted structured adapter. Side-effect failures and
interrupted claims are uncertain and cannot be automatically replayed. No
regular chat/CLI/SDK/browser/delegation adapter is enabled for Always-On. The
feature remains disabled until the coordinator supplies a restricted adapter
registry and opt-in responsibilities. This is not yet a running Always-On agent.
