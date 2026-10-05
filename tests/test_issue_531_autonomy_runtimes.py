import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from autonomy_models import ModelConfig, ModelSettings, create_model_router
from autonomy_runtimes import (
    RuntimeCompletion,
    RuntimeUnavailable,
    bounded_process,
    parse_result,
)
from autonomy_runtime_worker import acp_complete
from test_issue_527_autonomy_models import setup

RUNTIMES = [
    "copilot",
    "copilot-sdk",
    "claude",
    "claude-sdk",
    "opencode",
    "gemini",
    "codex",
    "cursor",
    "devin",
    "wee",
    "router",
]


def test_settings_migrate_old_json_and_preserve_provider(tmp_path):
    cfg = ModelSettings(tmp_path)
    legacy = asdict(ModelConfig())
    del legacy["routine_runtime"]
    del legacy["escalation_runtime"]
    legacy["routine_model"] = "ollama/llama3.2"
    cfg.path.write_text(json.dumps(legacy))
    loaded = cfg.load()
    assert loaded.routine_runtime == "wee" and loaded.routine_model == "ollama/llama3.2"
    cfg.save(asdict(loaded))
    assert json.loads(cfg.path.read_text())["defaults"]["routine_runtime"] == "wee"


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_every_wee_runtime_selectable_and_persistent(tmp_path, runtime):
    cfg = ModelSettings(tmp_path)

    def auth():
        return {"auth_type": "shared_key"}

    class Planner:
        runtime_catalog = staticmethod(
            lambda _: {
                "runtimes": [
                    {"id": r, "label": r, "available": False} for r in RUNTIMES
                ],
                "models": [{"id": "gpt-6-luna", "label": "GPT-6-luna"}],
            }
        )
        usage = staticmethod(lambda agent="": {})

    app = FastAPI()
    app.include_router(create_model_router(cfg, Planner(), auth))
    client = TestClient(app)
    model = "openrouter/openai/gpt-4.1-mini" if runtime == "wee" else "gpt-6-luna"
    response = client.put(
        "/api/v1/autonomy/model-settings",
        json={
            **asdict(ModelConfig()),
            "routine_runtime": runtime,
            "routine_model": model,
        },
    )
    assert response.status_code == 200
    assert ModelSettings(tmp_path).load().routine_runtime == runtime
    assert ModelSettings(tmp_path).load().routine_model == model
    assert len(
        client.get("/api/v1/autonomy/runtime-catalog").json()["runtimes"]
    ) == len(RUNTIMES)
    bad = client.put(
        "/api/v1/autonomy/model-settings",
        json={**response.json()["config"], "routine_runtime": "forged"},
    )
    assert bad.status_code == 400


def test_runtime_catalog_and_settings_require_verified_auth(tmp_path):
    def reject():
        raise HTTPException(401)

    class Planner:
        pass

    app = FastAPI()
    app.include_router(create_model_router(ModelSettings(tmp_path), Planner(), reject))
    c = TestClient(app)
    assert c.get("/api/v1/autonomy/runtime-catalog").status_code == 401
    assert (
        c.put("/api/v1/autonomy/model-settings", json=asdict(ModelConfig())).status_code
        == 401
    )


def test_selected_runtime_exact_model_and_no_silent_wee_fallback(tmp_path):
    calls = []
    s, store, cfg, p, w, row = setup(
        tmp_path, lambda *_: pytest.fail("Wee fallback must not run")
    )
    cfg.save(
        {
            **asdict(ModelConfig()),
            "routine_runtime": "codex",
            "routine_model": "gpt-6-luna",
        }
    )

    def completion(runtime, model, messages, tokens, **kwargs):
        calls.append((runtime, model))
        assert not kwargs["cancelled"]()
        return '{"report":"Codex routine"}', {"total_tokens": 90}

    p.runtime_completion = completion
    w.step()
    assert calls == [("codex", "gpt-6-luna")]
    assert store.get(row["id"])["phase"] == "waiting"
    assert p.usage()["actual_tokens"] == 90
    w.close()


def test_disabled_runtime_never_launches_process(monkeypatch):
    executor = RuntimeCompletion(
        None,
        lambda: [{"id": "codex"}],
        lambda _: False,
        lambda _: pytest.fail("Must not launch"),
    )
    with pytest.raises(RuntimeUnavailable, match="unavailable or disabled"):
        executor("codex", "gpt-6-luna", [], 1024)


def test_router_brain_budgeted_target_frozen_and_constrained(tmp_path):
    calls = []
    s, store, cfg, p, w, row = setup(
        tmp_path, lambda *_: pytest.fail("No provider fallback")
    )
    cfg.save(
        {
            **asdict(ModelConfig()),
            "routine_runtime": "router",
            "routine_model": "auto",
            "daily_token_budget": 200000,
        }
    )

    class Executor:
        def route(self, messages, invoke):
            calls.append("route")
            invoke(
                "codex", "gpt-6-luna", [{"role": "user", "content": "Choose target"}]
            )
            return "claude-sdk", "haiku"

        def __call__(self, runtime, model, *args, **kwargs):
            calls.append((runtime, model))
            return (
                "routing answer" if runtime == "codex" else '{"report":"Routed report"}'
            ), {"total_tokens": 10}

    p.runtime_completion = Executor()
    w.step()
    assert calls == ["route", ("codex", "gpt-6-luna"), ("claude-sdk", "haiku")]
    w.step()
    assert len(calls) == 3
    assert p.usage()["requests"] == 2
    w.close()


def test_timeout_and_cancellation_kill_planning_process(tmp_path):
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(RuntimeUnavailable, match="time limit"):
        bounded_process(
            cmd, "", tmp_path, os.environ.copy(), lambda: False, timeout=0.1
        )
    with pytest.raises(RuntimeUnavailable, match="cancelled"):
        bounded_process(cmd, "", tmp_path, os.environ.copy(), lambda: True)


def test_output_bound_and_stderr_secrets_not_returned(tmp_path):
    with pytest.raises(RuntimeUnavailable, match="output bound"):
        bounded_process(
            [sys.executable, "-c", 'print("x"*140000)'],
            "",
            tmp_path,
            os.environ.copy(),
            lambda: False,
        )
    with pytest.raises(RuntimeUnavailable) as error:
        bounded_process(
            [
                sys.executable,
                "-c",
                'import sys;print("PRIVATE-CREDENTIAL",file=sys.stderr);sys.exit(1)',
            ],
            "",
            tmp_path,
            os.environ.copy(),
            lambda: False,
        )
    assert "PRIVATE-CREDENTIAL" not in str(error.value)


def test_codex_no_tool_catalog_and_deny_hook(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex/models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-6-luna",
                        "apply_patch_tool_type": "freeform",
                        "experimental_supported_tools": ["list_dir"],
                        "supports_search_tool": True,
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    import autonomy_runtimes

    original_run = autonomy_runtimes.subprocess.run

    class Help:
        stdout = "--ignore-user-config --ignore-rules"

    monkeypatch.setattr(autonomy_runtimes.subprocess, "run", lambda *a, **k: Help())
    r = RuntimeCompletion(None, lambda: [], lambda _: True, lambda _: "/trusted/codex")
    cmd, parser = r.command("codex", "gpt-6-luna", tmp_path, {}, 1024)
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    catalog = json.loads((tmp_path / "models.json").read_text())["models"][0]
    assert (
        catalog["apply_patch_tool_type"] is None
        and not catalog["experimental_supported_tools"]
    )
    deny = json.loads(
        original_run(
            [sys.executable, str(tmp_path / "deny.py")], capture_output=True, text=True
        ).stdout
    )
    assert deny["decision"] == "block"
    assert "features.shell_tool=false" in cmd and 'web_search="disabled"' in cmd
    with pytest.raises(RuntimeUnavailable):
        parse_result("codex", '{"type":"turn.failed"}')


@pytest.mark.parametrize("case", ["ok", "missing-plan", "missing-model"])
def test_devin_acp_requires_model_and_plan_and_denies_host_tools(
    tmp_path, monkeypatch, case
):
    script = tmp_path / "devin"
    script.write_text("""#!""" + sys.executable + """
import json,sys,os
case=os.environ['WEE_FAKE_ACP_CASE']
def send(value):print(json.dumps(value),flush=True)
for line in sys.stdin:
 v=json.loads(line);method=v.get('method');rid=v['id']
 if method=='initialize':
  assert v['params']['clientCapabilities']=={'terminal':False,'fs':{'readTextFile':False,'writeTextFile':False}};result={}
 elif method=='session/new':
  assert v['params']['mcpServers']==[]
  result={'sessionId':'test','modes':{'availableModes':[] if case=='missing-plan' else [{'id':'plan'}]},'models':{'availableModels':[] if case=='missing-model' else [{'modelId':'chosen'}]}}
 elif method=='session/set_mode':assert v['params']['modeId']=='plan';result={}
 elif method=='session/set_model':assert v['params']['modelId']=='chosen';result={}
 elif method=='session/prompt':
  send({'jsonrpc':'2.0','id':80000,'method':'session/request_permission','params':{'options':[{'optionId':'yes','kind':'allow_always'}]}})
  reply=json.loads(sys.stdin.readline());assert reply['result']['outcome']['outcome']=='cancelled'
  send({'jsonrpc':'2.0','id':80001,'method':'fs/read_text_file','params':{'path':'/outside/private'}})
  reply=json.loads(sys.stdin.readline());assert reply['error']['code']==-32601
  send({'jsonrpc':'2.0','method':'session/update','params':{'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'{"report":"Bounded ACP report"}'}}}})
  result={'stopReason':'end_turn'}
 else:raise Exception('Unexpected request')
 send({'jsonrpc':'2.0','id':rid,'result':result})
""")
    script.chmod(0o700)
    monkeypatch.setenv("WEE_FAKE_ACP_CASE", case)
    if case == "ok":
        result = asyncio.run(
            acp_complete(str(script), "chosen", str(tmp_path), "Report only")
        )
        assert json.loads(result["result"])["report"] == "Bounded ACP report"
    else:
        with pytest.raises(ValueError):
            asyncio.run(
                acp_complete(str(script), "chosen", str(tmp_path), "Report only")
            )


def test_sdk_json_fence_uses_one_routine_call_without_escalation(tmp_path):
    calls = []

    def complete(*args):
        calls.append(1)
        return '```json\n{"report":"Verified SDK report"}\n```', {}

    s, store, cfg, p, w, row = setup(tmp_path, complete)
    w.step()
    assert calls == [1] and store.get(row["id"])["phase"] == "waiting"
    w.close()


def test_corrupt_partial_model_json_is_not_silently_defaulted(tmp_path):
    cfg = ModelSettings(tmp_path)
    cfg.path.write_text('{"routine_model":"ollama/x"}')
    with pytest.raises(ValueError, match="Unsupported"):
        cfg.load()
