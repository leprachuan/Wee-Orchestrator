"""Isolated process for SDK planning transports; stdout contains only the result."""

import asyncio
import json
import sys


async def acp_complete(binary, model, directory, prompt):
    process = await asyncio.create_subprocess_exec(
        binary,
        "acp",
        cwd=directory,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=131072,
    )
    pending, text = {}, []
    counter = 0

    async def send(value):
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()

    async def reader():
        try:
            async for raw in process.stdout:
                value = json.loads(raw)
                if "method" in value and "id" in value:
                    if value["method"] == "session/request_permission":
                        await send(
                            {
                                "jsonrpc": "2.0",
                                "id": value["id"],
                                "result": {"outcome": {"outcome": "cancelled"}},
                            }
                        )
                    else:
                        await send(
                            {
                                "jsonrpc": "2.0",
                                "id": value["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "Always-On host tools disabled",
                                },
                            }
                        )
                elif "id" in value and value["id"] in pending:
                    future = pending.pop(value["id"])
                    if "error" in value:
                        future.set_exception(
                            ValueError("ACP rejected required planning configuration")
                        )
                    else:
                        future.set_result(value.get("result", {}))
                elif value.get("method") == "session/update":
                    update = value.get("params", {}).get("update", {})
                    if update.get("sessionUpdate") == "agent_message_chunk":
                        content = update.get("content", {})
                        if content.get("type") == "text":
                            text.append(content.get("text", ""))
                    if sum(len(t) for t in text) > 16384:
                        raise ValueError("ACP report exceeds bound")
        finally:
            for future in pending.values():
                if not future.done():
                    future.set_exception(ValueError("ACP transport ended"))

    read_task = asyncio.create_task(reader())

    async def rpc(method, params):
        nonlocal counter
        counter += 1
        future = asyncio.get_running_loop().create_future()
        pending[counter] = future
        await send(
            {"jsonrpc": "2.0", "id": counter, "method": method, "params": params}
        )
        return await asyncio.wait_for(future, 75)

    try:
        await rpc(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "wee-always-on-plan", "version": "1"},
                "clientCapabilities": {
                    "terminal": False,
                    "fs": {"readTextFile": False, "writeTextFile": False},
                },
            },
        )
        session = await rpc("session/new", {"cwd": directory, "mcpServers": []})
        sid = session.get("sessionId")
        if not sid:
            raise ValueError("ACP did not create an isolated session")
        modes = session.get("modes", {}).get("availableModes", [])
        if "plan" not in {m.get("id") for m in modes}:
            raise ValueError("ACP server does not advertise enforceable plan mode")
        await rpc("session/set_mode", {"sessionId": sid, "modeId": "plan"})
        models = session.get("models", {}).get("availableModels", [])
        options = session.get("configOptions", [])
        if model in {m.get("modelId") for m in models}:
            await rpc("session/set_model", {"sessionId": sid, "modelId": model})
        else:
            option = next(
                (
                    o
                    for o in options
                    if o.get("category") == "model"
                    and model in {v.get("value") for v in o.get("options", [])}
                ),
                None,
            )
            if not option:
                raise ValueError("ACP cannot confirm requested model")
            await rpc(
                "session/set_config_option",
                {"sessionId": sid, "configId": option["id"], "value": model},
            )
        await rpc(
            "session/prompt",
            {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]},
        )
        return {"result": "".join(text), "usage": {}}
    finally:
        read_task.cancel()
        if process.returncode is None:
            process.terminate()
        await process.wait()
        await asyncio.gather(read_task, return_exceptions=True)


async def complete(runtime, model, directory, tokens, prompt):
    if runtime == "devin":
        import shutil

        binary = shutil.which("devin")
        if not binary:
            raise ValueError("Devin executable unavailable")
        return await acp_complete(binary, model, directory, prompt)
    if runtime == "claude-sdk":
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            query,
            AssistantMessage,
            TextBlock,
            ResultMessage,
            PermissionResultDeny,
        )

        async def deny(*args):
            return PermissionResultDeny(message="Always-On planning tools are disabled")

        options = ClaudeAgentOptions(
            model=model,
            tools=[],
            mcp_servers={},
            setting_sources=[],
            permission_mode="dontAsk",
            can_use_tool=deny,
            cwd=directory,
            max_turns=1,
            settings='{"disableAllHooks":true}',
            extra_args={
                "strict-mcp-config": None,
                "disable-slash-commands": None,
                "no-session-persistence": None,
            },
        )
        result, usage = [], {}
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                result.extend(
                    b.text for b in message.content if isinstance(b, TextBlock)
                )
            if isinstance(message, ResultMessage):
                if message.is_error:
                    raise ValueError("Claude SDK planning failed")
                usage = message.usage or {}
        return {"result": "".join(result), "usage": usage}
    if runtime == "copilot-sdk":
        from copilot import CopilotClient
        from copilot.generated.rpc import PermissionDecisionDeniedByRules

        client = CopilotClient(working_directory=directory, log_level="none")

        async def deny(*args):
            return PermissionDecisionDeniedByRules()

        await client.start()
        try:
            session = await client.create_session(
                model=model,
                working_directory=directory,
                available_tools=[],
                tools=[],
                mcp_servers={},
                on_permission_request=deny,
                enable_config_discovery=False,
                skip_custom_instructions=True,
                enable_file_hooks=False,
                enable_host_git_operations=False,
                enable_skills=False,
                custom_agents=[],
                plugin_directories=[],
                enable_session_store=False,
                manage_schedule_enabled=False,
            )
            try:
                event = await session.send_and_wait(prompt, timeout=75)
                return {"result": event.data.content if event else "", "usage": {}}
            finally:
                await session.disconnect()
        finally:
            await client.stop()
    raise ValueError("Unsupported SDK transport")


if __name__ == "__main__":
    runtime, model, directory, tokens = sys.argv[1:]
    try:
        result = asyncio.run(
            complete(runtime, model, directory, int(tokens), sys.stdin.read())
        )
        print(json.dumps(result))
    except Exception:
        # Provider exceptions may contain credential-bearing configuration.
        print(
            json.dumps(
                {"error": "SDK runtime failed; check authentication and selected model"}
            )
        )
        sys.exit(1)
