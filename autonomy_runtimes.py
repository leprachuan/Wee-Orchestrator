"""Planning-only runtime transports. Never reuse Wee's unrestricted chat dispatch."""

import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time


class RuntimeUnavailable(ValueError):
    pass


class RuntimeCompletion:
    def __init__(self, manager, registry, available, executable):
        self.manager, self.registry = manager, registry
        self.available, self.executable = available, executable

    def catalog(self, runtime=""):
        entries = [{**r, "available": self.available(r["id"])} for r in self.registry()]
        if runtime and runtime not in {r["id"] for r in entries}:
            raise RuntimeUnavailable("Unknown Wee runtime")
        groups = self.manager.get_models_for_runtime(runtime) if runtime else {}
        models = [
            {"id": model, "label": model, "group": group}
            for group, values in groups.items()
            for model in values
            if isinstance(model, str)
        ]
        return {"runtimes": entries, "models": models}

    def route(self, messages, invoke):
        from agent_manager import get_llm_router

        decision = get_llm_router().route(
            prompt=messages[-1]["content"],
            last_routed=None,
            runtime_available=lambda rt: rt != "router" and self.available(rt),
            invoke_brain=lambda rt, model, prompt, timeout: invoke(
                rt, model, [{"role": "user", "content": prompt}]
            ),
            resolve_model=self.manager.get_model_from_name,
        )
        if not decision.runtime or decision.runtime == "router":
            raise RuntimeUnavailable(
                "Configured Router did not select an available bounded runtime"
            )
        return decision.runtime, decision.model

    def __call__(
        self, runtime, model, messages, max_tokens, *, cancelled=lambda: False
    ):
        if runtime not in {r["id"] for r in self.registry()} or not self.available(
            runtime
        ):
            raise RuntimeUnavailable(
                "Selected runtime is unavailable or disabled on this API host"
            )
        if runtime == "wee":
            from autonomy_models import provider_completion

            return provider_completion(model, messages, max_tokens)
        if runtime == "router":
            raise RuntimeUnavailable(
                "Router requires a bounded routing adapter; unrestricted routing is disabled for Always-On"
            )
        with tempfile.TemporaryDirectory(prefix="wee-always-on-plan-") as directory:
            root = Path(directory)
            prompt = "\n\n".join(m["role"] + ": " + m["content"] for m in messages)
            env = dict(os.environ)
            # Do not pass the API credentials or scheduler permission overrides.
            for key in list(env):
                if key.startswith(
                    ("API_", "COPILOT_ALLOW", "WEE_SHELL", "WEE_BROWSER")
                ):
                    env.pop(key, None)
            command, parser = self.command(runtime, model, root, env, max_tokens)
            # Copilot -p does not append stdin. Pass the bounded prompt as one
            # argv element, never through a shell and never log the command.
            command = [
                prompt if arg == "__WEE_PLANNING_PROMPT__" else arg for arg in command
            ]
            output = bounded_process(command, prompt, root, env, cancelled)
            return parse_result(parser, output)

    def command(self, runtime, model, root, env, max_tokens):
        binaries = {
            "codex": "codex",
            "claude": "claude",
            "copilot": "copilot",
            "gemini": "gemini",
            "opencode": "opencode",
            "cursor": "cursor-agent",
            "devin": "devin",
        }
        if runtime in ("claude-sdk", "copilot-sdk", "devin"):
            return [
                sys.executable,
                str(Path(__file__).with_name("autonomy_runtime_worker.py")),
                runtime,
                model,
                str(root),
                str(max_tokens),
            ], "worker"
        binary = self.executable(binaries.get(runtime, runtime))
        if not binary:
            raise RuntimeUnavailable("Selected runtime executable is unavailable")
        if runtime == "codex":
            # Current exec supports an isolated config while retaining existing auth.
            help_text = subprocess.run(
                [binary, "exec", "--help"], capture_output=True, text=True, timeout=10
            ).stdout
            if (
                "--ignore-user-config" not in help_text
                or "--ignore-rules" not in help_text
            ):
                raise RuntimeUnavailable(
                    "Codex needs an exec version supporting isolated user configuration"
                )
            deny = root / "deny.py"
            deny.write_text(
                'import json\nprint(json.dumps({"decision":"block","reason":"Always-On planning has no tool permissions"}))\n'
            )
            catalog_path = (
                Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
                / "models_cache.json"
            )
            try:
                document = json.loads(catalog_path.read_text())
                entries = document.get("models", [])
                selected = next(
                    (dict(e) for e in entries if e.get("slug") == model), None
                )
                if selected is None:
                    raise RuntimeUnavailable(
                        "Selected Codex model is absent from this host's authenticated model catalog; refresh Codex models"
                    )
                selected.update(
                    apply_patch_tool_type=None,
                    experimental_supported_tools=[],
                    supports_search_tool=False,
                )
                catalog = root / "models.json"
                catalog.write_text(json.dumps({"models": [selected]}))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeUnavailable(
                    "Codex authenticated model catalog is unavailable"
                ) from exc
            instructions = root / "instructions.txt"
            instructions.write_text(
                "You are a Wee planning engine. Return only the requested bounded JSON report. No tools or actions are permitted."
            )
            cmd = [
                binary,
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "-m",
                model,
                "-C",
                str(root),
            ]
            for setting in (
                'approval_policy="never"',
                'web_search="disabled"',
                'model_reasoning_effort="low"',
                'history.persistence="none"',
                "project_doc_max_bytes=0",
                "model_instructions_file=" + json.dumps(str(instructions)),
                "mcp_servers={}",
                "model_catalog_json=" + json.dumps(str(catalog)),
                'hooks.PreToolUse=[{matcher=".*",hooks=[{type="command",command='
                + json.dumps(sys.executable + " " + str(deny))
                + "}]}]",
            ):
                cmd += ["-c", setting]
            for feature in (
                "shell_tool",
                "unified_exec",
                "apply_patch_freeform",
                "multi_agent",
                "multi_agent_v2",
                "apps",
                "plugins",
                "browser_use",
                "computer_use",
                "in_app_browser",
                "image_generation",
                "code_mode",
                "memories",
                "shell_snapshot",
                "tool_search",
                "tool_suggest",
                "workspace_dependencies",
            ):
                cmd += ["-c", "features." + feature + "=false"]
            cmd += ["-c", "features.codex_hooks=true", "-"]
            return cmd, "codex"
        if runtime == "claude":
            return [
                binary,
                "-p",
                "--model",
                model,
                "--output-format",
                "json",
                "--tools",
                "",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--setting-sources",
                "",
                "--settings",
                '{"disableAllHooks":true}',
                "--disable-slash-commands",
                "--no-session-persistence",
                "--permission-mode",
                "dontAsk",
            ], "claude"
        if runtime == "copilot":
            # available-tools is an empty allowlist; deny all is a second boundary.
            return [
                binary,
                "-p",
                "__WEE_PLANNING_PROMPT__",
                "--model",
                model,
                "--silent",
                "--available-tools=",
                "--deny-tool",
                "shell",
                "--deny-tool",
                "write",
                "--deny-tool",
                "url",
                "--disallow-temp-dir",
                "--disable-builtin-mcps",
                "--no-custom-instructions",
                "--log-level",
                "none",
            ], "text"
        if runtime == "gemini":
            config = root / "gemini-settings.json"
            config.write_text(
                json.dumps(
                    {
                        "tools": {"core": [], "exclude": ["*"]},
                        "hooksConfig": {"enabled": False},
                        "admin": {
                            "extensions": {"enabled": False},
                            "mcp": {"enabled": False},
                            "skills": {"enabled": False},
                        },
                        "skills": {"enabled": False},
                        "mcpServers": {},
                        "context": {"fileName": []},
                    }
                )
            )
            env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(config)
            policy = root / "deny.toml"
            policy.write_text(
                '[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n'
            )
            return [
                binary,
                "-p",
                "Return the requested JSON from stdin context only.",
                "-m",
                model,
                "-o",
                "json",
                "--admin-policy",
                str(policy),
                "--extensions",
                "none",
            ], "gemini"
        if runtime == "opencode":
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
                {
                    "permission": {"*": "deny"},
                    "tools": {"*": False},
                    "mcp": {},
                    "plugin": [],
                    "instructions": [],
                    "agent": {
                        "wee-always-on-plan": {
                            "mode": "primary",
                            "permission": {"*": "deny"},
                            "tools": {"*": False},
                            "prompt": "Return bounded report JSON from supplied context only. No tools.",
                        }
                    },
                }
            )
            env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "true"
            env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
            return [
                binary,
                "run",
                "--agent",
                "wee-always-on-plan",
                "--model",
                model,
                "--format",
                "json",
                "--dir",
                str(root),
            ], "opencode"
        if runtime == "cursor":
            help_text = subprocess.run(
                [binary, "--help"], capture_output=True, text=True, timeout=10
            ).stdout
            if "--mode" not in help_text:
                raise RuntimeUnavailable(
                    "Cursor needs a version supporting explicit ask mode"
                )
            cursor = root / ".cursor"
            cursor.mkdir()
            (cursor / "cli.json").write_text(
                json.dumps(
                    {
                        "permissions": {
                            "allow": [],
                            "deny": [
                                "Shell(*)",
                                "Read(**)",
                                "Read(/**)",
                                "Write(**)",
                                "Write(/**)",
                                "WebFetch(*)",
                                "Mcp(*:*)",
                            ],
                        }
                    }
                )
            )
            (cursor / "mcp.json").write_text('{"mcpServers":{}}')
            deny = cursor / "deny.py"
            deny.write_text(
                'import json\nprint(json.dumps({"permission":"deny","decision":"deny","user_message":"Always-On tools disabled","agent_message":"Return the report using supplied context only."}))\n'
            )
            (cursor / "hooks.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "hooks": {
                            event: [
                                {
                                    "command": sys.executable + " " + str(deny),
                                    "failClosed": True,
                                }
                            ]
                            for event in (
                                "preToolUse",
                                "beforeShellExecution",
                                "beforeReadFile",
                                "beforeMCPExecution",
                                "subagentStart",
                            )
                        },
                    }
                )
            )
            return [
                binary,
                "-p",
                "--mode",
                "ask",
                "--model",
                model,
                "--output-format",
                "json",
            ], "claude"
        raise RuntimeUnavailable(
            "No planning transport is registered for the selected runtime"
        )


def bounded_process(command, prompt, cwd, env, cancelled, timeout=90):
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    output = bytearray()
    total = 0
    try:
        process.stdin.write(prompt.encode())
        process.stdin.close()
        selector.register(process.stdout, selectors.EVENT_READ, True)
        selector.register(process.stderr, selectors.EVENT_READ, False)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            if cancelled():
                raise RuntimeUnavailable("Always-On run paused, revised or cancelled")
            if time.monotonic() >= deadline:
                raise RuntimeUnavailable("Planning runtime exceeded the time limit")
            for key, _ in selector.select(0.2):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                total += len(data)
                if total > 131072:
                    raise RuntimeUnavailable(
                        "Planning runtime exceeded the output bound"
                    )
                if key.data:
                    output.extend(data)
        if process.wait(timeout=2):
            raise RuntimeUnavailable(
                "Selected runtime failed; check its authentication/model on the API host. No fallback attempted"
            )
        return output.decode()
    finally:
        selector.close()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()


def parse_result(parser, output):
    if parser == "text":
        return output.strip(), {}
    if parser in ("claude", "gemini", "worker"):
        data = json.loads(output)
        if data.get("is_error") or data.get("error"):
            raise RuntimeUnavailable("Selected runtime reported an error")
        usage = data.get("usage", {})
        if (
            isinstance(usage, dict)
            and "input_tokens" in usage
            and "output_tokens" in usage
        ):
            usage = {"total_tokens": usage["input_tokens"] + usage["output_tokens"]}
        return data.get("result", data.get("response", "")), usage
    text, usage = [], {}
    for line in output.splitlines():
        data = json.loads(line)
        if parser == "codex":
            item = data.get("item", {})
            if (
                data.get("type") == "item.completed"
                and item.get("type") == "agent_message"
            ):
                text.append(item.get("text", ""))
            if data.get("type") == "turn.completed":
                u = data.get("usage", {})
                if (
                    type(u.get("input_tokens")) is int
                    and type(u.get("output_tokens")) is int
                ):
                    usage = {"total_tokens": u["input_tokens"] + u["output_tokens"]}
            if data.get("type") in ("error", "turn.failed"):
                raise RuntimeUnavailable(
                    "Codex failed with the selected model; no fallback attempted"
                )
        elif data.get("type") == "text":
            text.append(data.get("part", {}).get("text", ""))
    return "".join(text), usage
