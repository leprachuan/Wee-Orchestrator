# Historical documents

Point-in-time write-ups kept for provenance: completion summaries, bug
investigations, and feature notes from when a change was made. They are **not
maintained** and may describe behaviour that has since changed — most notably the
Wee runtime, which moved onto the Copilot SDK's agentic loop in #443 and no
longer drives its own tool-calling rounds.

For current behaviour see:

| Topic | Document |
|---|---|
| Overview and install | [`../../README.md`](../../README.md) |
| Runtimes, tools, operations | [`../OPERATIONS_GUIDE.md`](../OPERATIONS_GUIDE.md) |
| Local runtime and clients | [`../LOCAL_RUNTIME_AND_CLIENTS_2026-07.md`](../LOCAL_RUNTIME_AND_CLIENTS_2026-07.md) |
| API releases and upgrades | [`../API_RELEASES.md`](../API_RELEASES.md) |

Moved out of the repository root as part of the documentation reconciliation in
issue #452, so the root holds current reference material rather than a mix of
both. Nothing was deleted; use `git log --follow` for a file's history.
