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

## Persistent responsibilities (#526)
The API can create bounded responsibilities for existing agent identities. They
start paused, with a 5-minute minimum schedule and maximum 20 retained active
responsibilities. Resume is explicit opt-in; pause/cancel stop pending execution.
Goal revision pauses and discards the pending plan. Restarted model work or
claimed actions require human reconciliation, while waiting approvals retain
the same intent and plan. One worker has an exclusive OS lease; each tick handles
at most one responsibility without overlap.

Initial adapter scope is deliberately limited to drafting bounded reports in
private per-responsibility workspaces on the API host. Model output is data, not
executable commands; there are no regular chat/CLI/SDK/browser/delegation tools.
The report body is an explicit approval preview. Writes use anchored directory
descriptors without symlink traversal, atomic replacement and private files.
Rules may grant this exact capability; deny and revocation still take precedence.
General app connectors and external host actions need separate trusted adapters.
The model/budget provider and worker lifecycle wiring are the final stage.

## Routine models and budgets (#527)
`/api/v1/autonomy/model-settings` exposes a private JSON configuration and current
UTC daily usage. Default routine model: provider-qualified OpenRouter GPT-4.1
mini; choose a supported inexpensive provider model (including Luna if your
provider exposes it). OpenRouter, Ollama and LM Studio use the existing Wee
provider resolver. No external tool execution is available to the model.

Default caps: 3 requests per run, 1024 output tokens, 20 requests and 40000
conservatively reserved tokens per UTC day across responsibilities. Reservations
commit before network I/O and are not refunded after ambiguous failures. Missing
provider usage stays unknown. Dollar cost is explicitly unavailable; provider
pricing is not assumed. Scheduling, scope, schema, budget and retry checks are
ordinary deterministic code.

Only two recorded failed report-schema checks can propose escalation to an
explicitly permitted model. The model change passes the same shared action gate,
rechecks its allowlist and budget immediately before execution, and affects one
request. Every new run starts with the routine model again. No allowed model or
budget means stop for human review. Network/auth failure does not trigger a
larger model. A revised/cancelled responsibility invalidates stale in-flight plans.

Initial supported activity: observe API agent/queue counts and maintain bounded
report drafts with persistent prior context. Reports require approval unless an
explicit file-write rule allows them. General external tools/hosts, arbitrary
shell, browser, delegation and background iOS push remain unsupported. Clients
refresh connected state every three seconds; the API also provides bounded
30-second authenticated SSE connections with durable event IDs for replay.
Offline clients fetch authoritative state on reconnect. Approval history is capped
at 10000 records (100 pending), responsibility history at 1000, policies at 1000
rules and model run audit at 10000; reaching capacity stops new work for operator
archival instead of silently deleting audit evidence.

Private state failure disables Always-On and leaves ordinary API health/chat
available. One exclusive worker starts with API lifespan; shutdown waits for its
bounded adapter call before releasing the lease. Interrupted effects are not
blindly retried. UI responsibilities begin paused and support resume, pause,
cancel, goal revision and explicit uncertainty acknowledgement.

## Configurable runtimes and models (#531)
Settings persist `routine_runtime` / `routine_model` independently from
`escalation_runtime` / `escalation_models` in server `model-budgets.json`. Existing
provider-only files migrate to runtime `wee` without losing model choices or
budgets. macOS, iOS and WebUI offer the authenticated API host's runtime registry
and model catalog, with manual exact model entry. All Wee runtime entries are
selectable: Copilot CLI/SDK, Claude CLI/SDK, OpenCode, Gemini, Codex, Cursor,
Devin, Wee, and Router. Authentication, installed versions, disabled-runtime
settings and account model access still determine availability. No silent
substitution is made when a selection cannot execute.

Planning uses dedicated transports in temporary workspaces, not the ordinary
chat dispatcher. CLI/SDK tool allowlists and permission denials disable actions;
Codex additionally ignores user configuration/rules, uses read-only mode, a
stripped model tool catalog, disabled hosted tools and a deny hook. Cursor uses
ask mode, deny permissions and fail-closed tool hooks. Devin requires an
advertised plan mode and an acknowledged exact model before prompting, and
refuses all ACP host tool and permission requests. Older installations lacking
these controls stop for review. External effects remain limited to the existing
server-gated report adapter. Vendor-managed remote tool behavior must be validated
on each installed runtime version before enabling production Always-On there.

Router uses the existing configured router through bounded planning adapters.
Brain requests consume the same durable budget, recursive routing is forbidden,
and resolved runtime/model pairs are saved for the current run. Escalation
approvals bind the resolved pair rather than a generic routing label. Each new
run returns to the configured routine choice.

CLI/SDK calls have a 90-second wall-clock limit, bounded output, cancellation
and process-group cleanup. Token caps exposed by providers are enforced; vendor
CLI/SDK output-token limits are best effort. Such runtimes reserve extra vendor
framing overhead, reconcile actual usage when available, and stop on overruns.
Unknown usage and dollar cost are shown honestly.

Dev's currently installed Codex catalog advertises GPT-5.5 and GPT-5.4 variants;
GPT-6-luna is not advertised by that host/account at this validation checkpoint.
Selecting GPT-6-luna retains that exact identifier and stops with an availability
error until the API host's authenticated Codex catalog supports it. It does not
change this development chat's coding model.
