# Local Runtime and Client Update — July 2026

## API and Wee runtime

- Local app-managed API instances use an explicit, per-user agent configuration
  file instead of inheriting the remote deployment's `agents.json`.
- The API settings endpoints resolve that configured agent file consistently,
  allowing local agents to be created, updated, reloaded, and deleted without
  modifying remote agents.
- First launch can bootstrap the local Python environment from
  `requirements.txt`; the macOS client can also clone and fast-forward a local
  API checkout before starting it.
- Local app-managed API instances use a private shared-key authentication
  boundary. The key is supplied only to the child process; clients retain the
  bearer token in their platform credential store.
- Session-token lifetime was extended for mobile/client pairing flows, and
  session resets are surfaced to clients instead of being silently treated as
  normal transcript continuation.
- Kanban label updates persist user-defined labels.

## Wee native runtime

- `ollama/...` models honor `WEE_OLLAMA_HOST`, so a local API uses the Ollama
  runner on the same Mac instead of the server deployment's default host.
- The runtime exposes `bash`, `python`, `call_agent`, and `search` as actual
  tool definitions. Search uses a configured SearXNG endpoint when available
  and a public-search fallback otherwise.
- A completed search ends the tool turn with sourced results. This prevents
  smaller local models from repeatedly requesting search or ending with an
  empty response after announcing a search.
- The compact system prompt is now the default for every runtime to preserve
  usable context for local models.

## Client integration contract

Local clients start the API with these non-secret configuration concepts:

- `APP_ENV=LOCAL`
- an isolated agent-config file path
- `WEE_OLLAMA_HOST` for the local model runner
- optional `OPENROUTER_API_KEY` supplied from the platform credential store

No secret values belong in source control, release notes, or build artifacts.

## Validation

- Wee runtime/search regression suites pass locally.
- The local API health endpoint was verified after restart.
- The local Targa search exercise returned sourced Targa listing and price
  information through the runtime search tool.

