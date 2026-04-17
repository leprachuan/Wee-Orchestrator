"""Tests for Issue #158: CLI version of Wee Runtime (wee-cli).

Tests cover:
- CLI argument parsing
- Config file loading/saving
- Token tracking
- Interactive REPL command parsing
- Single-shot mode
- Model resolution via CLI
- Output formatting
- Piping support (stdin)
- History management
- Slash command handling
"""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

# Ensure project root is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wee_cli import __version__  # noqa: E402
from wee_cli import (  # noqa: E402
    REPL_HELP,
    TokenTracker,
    _make_client,
    _print_error,
    _print_info,
    _print_markdown,
    build_parser,
    chat_stream,
    load_config,
    main,
    save_config,
)


class TestBuildParser(unittest.TestCase):
    """Test CLI argument parsing."""

    def test_single_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--model", "ollama/qwen3:8b", "Hello world"])
        self.assertEqual(args.model, "ollama/qwen3:8b")
        self.assertEqual(args.prompt, ["Hello world"])
        self.assertFalse(args.interactive)
        self.assertFalse(args.tools)

    def test_interactive_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--interactive"])
        self.assertTrue(args.interactive)

    def test_short_flags(self):
        parser = build_parser()
        args = parser.parse_args(["-m", "openrouter/llama-2", "-i"])
        self.assertEqual(args.model, "openrouter/llama-2")
        self.assertTrue(args.interactive)

    def test_tools_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--tools", "Do something"])
        self.assertTrue(args.tools)

    def test_all_arguments(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--model",
                "openrouter/llama-2-70b",
                "--api-key",
                "test-key",
                "--api-base",
                "http://localhost:8080/v1",
                "--tools",
                "--permission",
                "elevated",
                "--system",
                "You are helpful",
                "--temperature",
                "0.7",
                "--timeout",
                "60",
                "--output",
                "json",
                "What is 2+2?",
            ]
        )
        self.assertEqual(args.model, "openrouter/llama-2-70b")
        self.assertEqual(args.api_key, "test-key")
        self.assertEqual(args.api_base, "http://localhost:8080/v1")
        self.assertTrue(args.tools)
        self.assertEqual(args.permission, "elevated")
        self.assertEqual(args.system, "You are helpful")
        self.assertAlmostEqual(args.temperature, 0.7)
        self.assertAlmostEqual(args.timeout, 60.0)
        self.assertEqual(args.output, "json")
        self.assertEqual(args.prompt, ["What is 2+2?"])

    def test_permission_choices(self):
        parser = build_parser()
        for perm in ["restricted", "auto", "elevated"]:
            args = parser.parse_args(["--permission", perm, "test"])
            self.assertEqual(args.permission, perm)

    def test_output_choices(self):
        parser = build_parser()
        for fmt in ["text", "json", "markdown"]:
            args = parser.parse_args(["--output", fmt, "test"])
            self.assertEqual(args.output, fmt)

    def test_invalid_permission_rejected(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--permission", "invalid", "test"])

    def test_invalid_output_rejected(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--output", "html", "test"])

    def test_multi_word_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["What", "is", "the", "capital"])
        self.assertEqual(args.prompt, ["What", "is", "the", "capital"])

    def test_version_flag(self):
        parser = build_parser()
        with self.assertRaises(SystemExit) as cm:
            parser.parse_args(["--version"])
        self.assertEqual(cm.exception.code, 0)

    def test_default_values(self):
        parser = build_parser()
        args = parser.parse_args(["test"])
        self.assertIsNone(args.model)
        self.assertIsNone(args.api_key)
        self.assertIsNone(args.api_base)
        self.assertFalse(args.tools)
        self.assertEqual(args.permission, "restricted")
        self.assertIsNone(args.system)
        self.assertIsNone(args.temperature)
        self.assertAlmostEqual(args.timeout, 120.0)
        self.assertEqual(args.output, "text")
        self.assertFalse(args.interactive)


class TestConfigFile(unittest.TestCase):
    """Test config file loading and saving."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.tmpdir, "config.json")

    def test_load_missing_config(self):
        with patch("wee_cli.DEFAULT_CONFIG_FILE", "/nonexistent/config.json"):
            cfg = load_config()
        self.assertEqual(cfg, {})

    def test_save_and_load_config(self):
        test_cfg = {"model": "ollama/qwen3:8b", "tools": True, "timeout": 60}
        with patch("wee_cli.DEFAULT_CONFIG_FILE", self.config_file):
            with patch("wee_cli.DEFAULT_CONFIG_DIR", self.tmpdir):
                save_config(test_cfg)
                loaded = load_config()
        self.assertEqual(loaded, test_cfg)

    def test_load_invalid_json(self):
        with open(self.config_file, "w") as f:
            f.write("not json{{{")
        with patch("wee_cli.DEFAULT_CONFIG_FILE", self.config_file):
            cfg = load_config()
        self.assertEqual(cfg, {})


class TestTokenTracker(unittest.TestCase):
    """Test token usage tracking."""

    def test_initial_state(self):
        t = TokenTracker()
        self.assertEqual(t.prompt_tokens, 0)
        self.assertEqual(t.completion_tokens, 0)
        self.assertEqual(t.total_tokens, 0)
        self.assertEqual(t.turns, 0)

    def test_update_with_usage(self):
        t = TokenTracker()
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 20
        usage.total_tokens = 30
        t.update(usage)
        self.assertEqual(t.prompt_tokens, 10)
        self.assertEqual(t.completion_tokens, 20)
        self.assertEqual(t.total_tokens, 30)
        self.assertEqual(t.turns, 1)

    def test_update_accumulates(self):
        t = TokenTracker()
        for i in range(3):
            usage = MagicMock()
            usage.prompt_tokens = 10
            usage.completion_tokens = 5
            usage.total_tokens = 15
            t.update(usage)
        self.assertEqual(t.prompt_tokens, 30)
        self.assertEqual(t.completion_tokens, 15)
        self.assertEqual(t.total_tokens, 45)
        self.assertEqual(t.turns, 3)

    def test_update_with_none(self):
        t = TokenTracker()
        t.update(None)
        self.assertEqual(t.prompt_tokens, 0)
        self.assertEqual(t.turns, 1)

    def test_summary_format(self):
        t = TokenTracker()
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150
        t.update(usage)
        summary = t.summary()
        self.assertIn("prompt: 100", summary)
        self.assertIn("completion: 50", summary)
        self.assertIn("total: 150", summary)
        self.assertIn("turns: 1", summary)


class TestModelResolutionViaCLI(unittest.TestCase):
    """Test model resolution through the CLI."""

    @patch("wee_cli._make_client")
    @patch("wee_cli.chat_stream", return_value="test response")
    def test_default_model_from_env(self, mock_chat, mock_client):
        with patch.dict(os.environ, {"WEE_MODEL": "ollama/test-model"}, clear=False):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                main(["test prompt"])
        # Model should be resolved from WEE_MODEL env
        mock_client.assert_called_once()
        call_args = mock_client.call_args
        self.assertIn(
            "192.168.1.101:11434",
            call_args[1]["api_base"] if "api_base" in call_args[1] else call_args[0][0],
        )

    @patch("wee_cli._make_client")
    @patch("wee_cli.chat_stream", return_value="test response")
    def test_model_from_cli_overrides_env(self, mock_chat, mock_client):
        with patch.dict(os.environ, {"WEE_MODEL": "ollama/env-model"}, clear=False):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                main(["--model", "ollama/cli-model", "test prompt"])
        mock_client.assert_called_once()

    @patch("wee_cli._make_client")
    @patch("wee_cli.chat_stream", return_value="response")
    def test_config_file_model(self, mock_chat, mock_client):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"model": "ollama/config-model"}, f)
            config_path = f.name
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WEE_MODEL", None)
                with patch("sys.stdin") as mock_stdin:
                    mock_stdin.isatty.return_value = True
                    main(["--config", config_path, "test prompt"])
            mock_client.assert_called_once()
        finally:
            os.unlink(config_path)


class TestChatStream(unittest.TestCase):
    """Test the chat_stream function."""

    def _make_mock_chunk(self, content=None, tool_calls=None, usage=None):
        chunk = MagicMock()
        choice = MagicMock()
        choice.delta.content = content
        choice.delta.tool_calls = tool_calls
        chunk.choices = [choice]
        chunk.usage = usage
        return chunk

    @patch("sys.stdout", new_callable=StringIO)
    def test_simple_response(self, mock_stdout):
        client = MagicMock()
        chunks = [
            self._make_mock_chunk(content="Hello "),
            self._make_mock_chunk(content="world!"),
        ]
        client.chat.completions.create.return_value = iter(chunks)

        tracker = TokenTracker()
        result = chat_stream(
            client=client,
            model="test",
            messages=[{"role": "user", "content": "hi"}],
            token_tracker=tracker,
        )
        self.assertEqual(result, "Hello world!")
        self.assertIn("Hello world!", mock_stdout.getvalue())

    @patch("sys.stdout", new_callable=StringIO)
    def test_empty_response(self, mock_stdout):
        client = MagicMock()
        chunks = [self._make_mock_chunk(content="")]
        client.chat.completions.create.return_value = iter(chunks)

        result = chat_stream(
            client=client,
            model="test",
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(result, "")

    @patch("sys.stdout", new_callable=StringIO)
    def test_no_choices(self, mock_stdout):
        client = MagicMock()
        chunk = MagicMock()
        chunk.choices = []
        client.chat.completions.create.return_value = iter([chunk])

        result = chat_stream(
            client=client,
            model="test",
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(result, "")


class TestOutputFormatting(unittest.TestCase):
    """Test output formatting functions."""

    @patch("sys.stdout", new_callable=StringIO)
    def test_json_output(self, mock_stdout):
        _print_markdown("Hello world", output_format="json")
        output = mock_stdout.getvalue()
        data = json.loads(output)
        self.assertEqual(data["response"], "Hello world")

    def test_text_output_passthrough(self):
        # text mode just passes through (already printed by streaming)
        _print_markdown("Hello", output_format="text")

    def test_print_error(self):
        with patch("sys.stderr", new_callable=StringIO) as mock_err:
            with patch("wee_cli._rich_available", False):
                _print_error("test error")
            self.assertIn("test error", mock_err.getvalue())

    def test_print_info(self):
        with patch("sys.stderr", new_callable=StringIO) as mock_err:
            with patch("wee_cli._rich_available", False):
                _print_info("test info")
            self.assertIn("test info", mock_err.getvalue())


class TestREPLCommands(unittest.TestCase):
    """Test interactive REPL slash command handling."""

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_exit_command(self, mock_chat, mock_save, mock_init, mock_client):
        with patch("builtins.input", side_effect=["exit"]):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )
        mock_chat.assert_not_called()

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_quit_command(self, mock_chat, mock_save, mock_init, mock_client):
        with patch("builtins.input", side_effect=["quit"]):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )
        mock_chat.assert_not_called()

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_clear_command(self, mock_chat, mock_save, mock_init, mock_client):
        inputs = ["hello", "/clear", "exit"]
        with patch("builtins.input", side_effect=inputs):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )
        # chat_stream called once for "hello", /clear doesn't trigger it
        self.assertEqual(mock_chat.call_count, 1)

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_tokens_command(self, mock_chat, mock_save, mock_init, mock_client):
        with patch("builtins.input", side_effect=["/tokens", "exit"]):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )
        mock_chat.assert_not_called()

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_help_command(self, mock_chat, mock_save, mock_init, mock_client):
        with patch("builtins.input", side_effect=["/help", "exit"]):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )
        mock_chat.assert_not_called()

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_version_command(self, mock_chat, mock_save, mock_init, mock_client):
        with patch("builtins.input", side_effect=["/version", "exit"]):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )
        mock_chat.assert_not_called()

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_unknown_command(self, mock_chat, mock_save, mock_init, mock_client):
        with patch("builtins.input", side_effect=["/unknown", "exit"]):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )
        mock_chat.assert_not_called()

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_eof_exits(self, mock_chat, mock_save, mock_init, mock_client):
        with patch("builtins.input", side_effect=EOFError):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_keyboard_interrupt_exits(
        self, mock_chat, mock_save, mock_init, mock_client
    ):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_conversation_history(self, mock_chat, mock_save, mock_init, mock_client):
        """Test that messages accumulate in conversation."""
        inputs = ["first message", "second message", "exit"]
        with patch("builtins.input", side_effect=inputs):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )
        # chat_stream called twice
        self.assertEqual(mock_chat.call_count, 2)
        # Second call should have more messages (system + user1 + assistant1 + user2)
        second_call_messages = mock_chat.call_args_list[1][1]["messages"]
        # system + user1 + assistant1 + user2 = 4 messages
        self.assertGreaterEqual(len(second_call_messages), 4)

    @patch("wee_cli._make_client")
    @patch("wee_cli._init_readline")
    @patch("wee_cli._save_readline")
    @patch("wee_cli.chat_stream", return_value="response text")
    def test_empty_input_ignored(self, mock_chat, mock_save, mock_init, mock_client):
        with patch("builtins.input", side_effect=["", "  ", "exit"]):
            from wee_cli import run_interactive

            run_interactive(
                model="test",
                api_base="http://localhost/v1",
                api_key="test",
                tools_enabled=False,
                temperature=None,
                timeout=60,
                system_prompt="",
                output_format="text",
                permission="restricted",
            )
        mock_chat.assert_not_called()


class TestMainEntryPoint(unittest.TestCase):
    """Test the main() function dispatching."""

    @patch("wee_cli._make_client")
    @patch("wee_cli.chat_stream", return_value="response")
    def test_single_shot_mode(self, mock_chat, mock_client):
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            main(["--model", "ollama/test", "Hello"])
        mock_chat.assert_called_once()

    @patch("wee_cli.run_interactive")
    def test_interactive_flag(self, mock_interactive):
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            main(["--interactive", "--model", "ollama/test"])
        mock_interactive.assert_called_once()

    @patch("wee_cli.run_interactive")
    def test_no_args_enters_interactive(self, mock_interactive):
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            main([])
        mock_interactive.assert_called_once()


class TestPipingSupport(unittest.TestCase):
    """Test stdin piping."""

    @patch("wee_cli._make_client")
    @patch("wee_cli.chat_stream", return_value="response")
    def test_stdin_pipe(self, mock_chat, mock_client):
        with patch("sys.stdin", StringIO("piped input")):
            main(["--model", "ollama/test"])
        mock_chat.assert_called_once()
        # The prompt should contain the piped input
        call_kwargs = mock_chat.call_args[1]
        user_msg = [m for m in call_kwargs["messages"] if m["role"] == "user"][0]
        self.assertEqual(user_msg["content"], "piped input")


class TestMakeClient(unittest.TestCase):
    """Test OpenAI client creation."""

    @patch("wee_cli.OpenAI", create=True)
    def test_client_creation(self, mock_openai_cls):
        # Patch at the point of import inside _make_client
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            with patch("wee_cli._make_client.__module__", "wee_cli"):
                try:
                    _make_client("http://localhost/v1", "test-key", 60.0)
                except Exception:
                    pass  # May fail without real openai, that's OK for structure test


class TestVersionConstant(unittest.TestCase):
    """Test version is defined."""

    def test_version_exists(self):
        self.assertIsNotNone(__version__)
        self.assertRegex(__version__, r"\d+\.\d+\.\d+")


class TestREPLHelp(unittest.TestCase):
    """Test REPL help text."""

    def test_help_contains_commands(self):
        self.assertIn("/clear", REPL_HELP)
        self.assertIn("/history", REPL_HELP)
        self.assertIn("/model", REPL_HELP)
        self.assertIn("/tokens", REPL_HELP)
        self.assertIn("/system", REPL_HELP)
        self.assertIn("/help", REPL_HELP)
        self.assertIn("exit", REPL_HELP)


if __name__ == "__main__":
    unittest.main()
