"""
Regression test for issue #453: on the `wee` runtime with a local Ollama model,
only the custom-registered tools (search, call_agent, browser) executed. The
Copilot SDK's built-in shell/file/python tools are not present on a BYOK route,
so the model had no way to run a command or touch a file. It improvised --
markdown code fences, pseudo-XML, invented function names -- and the turn ended
with nothing having run.

#443 stopped redeclaring shell/file tools on the reasoning that "the Copilot SDK
session already knows about and describes its own built-in tools". That holds
for Copilot-native models but not for BYOK providers, where the registered tool
list is the complete set.

These tests pin the provider gate, the executors, and the prompt text. They do
NOT cover the end-to-end model behaviour: that needs a live Ollama model, and
per the issue a single probe turn runs 100-280s and routinely hits the SDK's
260s idle timeout on that hardware.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_SHARED_KEY", "test_key_453")
os.environ.setdefault("APP_ENV", "DEV")
os.environ.setdefault("API_PORT", "9453")

import pytest  # noqa: E402

from wee_runtime import execute_bash, execute_python, sanitize_bash_command  # noqa: E402


def _manager_class():
    import agent_manager

    for name in dir(agent_manager):
        obj = getattr(agent_manager, name)
        if isinstance(obj, type) and hasattr(obj, "_wee_provider_needs_native_tools"):
            return obj
    raise AssertionError("No class exposing _wee_provider_needs_native_tools")


@pytest.mark.parametrize("provider", ["ollama", "lmstudio", "openrouter", "custom", ""])
def test_issue_453_byok_providers_get_native_tools(provider):
    manager = _manager_class()
    assert manager._wee_provider_needs_native_tools(provider) is True


@pytest.mark.parametrize("provider", ["copilot", "Copilot", "GITHUB", "githubcopilot"])
def test_issue_453_copilot_native_providers_do_not_redeclare_tools(provider):
    """#443's reasoning still applies where it was actually true."""
    manager = _manager_class()
    assert manager._wee_provider_needs_native_tools(provider) is False


def test_issue_453_bash_tool_actually_runs_a_command():
    output = execute_bash({"command": "echo WEE-453-OK"})
    assert output == "WEE-453-OK"


def test_issue_453_bash_reports_failure_output():
    output = execute_bash({"command": "ls /definitely/not/here/453"})
    assert "STDERR" in output


def test_issue_453_bash_rejects_empty_command():
    assert execute_bash({}).startswith("Error")


def test_issue_453_python_tool_actually_evaluates_code():
    output = execute_python({"code": "print(7919 * 104729)"})
    assert output == str(7919 * 104729)


def test_issue_453_python_rejects_empty_code():
    assert execute_python({}).startswith("Error")


def test_issue_453_ssh_commands_keep_host_key_protection():
    """Preserved from #111: accept-new still rejects CHANGED keys."""
    sanitized = sanitize_bash_command("ssh root@192.168.1.100 'uptime'")
    assert "StrictHostKeyChecking=accept-new" in sanitized

    already_set = "ssh -o StrictHostKeyChecking=no host 'uptime'"
    assert sanitize_bash_command(already_set) == already_set


def test_issue_453_prompt_describes_bash_and_python_only_when_registered():
    manager = _manager_class()
    instance = manager.__new__(manager)

    byok = manager._wee_augment_system_prompt_with_tools(
        instance, "BASE", native_tools_registered=True
    )
    assert "**bash**" in byok
    assert "**python**" in byok
    # The failure mode was the model emitting a fenced command instead of
    # calling a tool, so the prompt must say that explicitly.
    assert "markdown code block" in byok

    native = manager._wee_augment_system_prompt_with_tools(
        instance, "BASE", native_tools_registered=False
    )
    assert "**bash**" not in native
    assert "**search**" in native, "custom tools are still described either way"
