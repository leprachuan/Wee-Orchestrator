"""Issue #198: buildTimingText returns mixed types (DocumentFragment vs string).

Regression tests ensuring buildTimingText always returns string|null,
never a DocumentFragment.
"""

import os
import re
import subprocess
import textwrap
import unittest

APP_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "webui", "dist", "app.js",
)


def _get_app_js():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_build_timing_text(source):
    """Extract just the buildTimingText function body."""
    pattern = r"function buildTimingText\(.*?\n\}"
    m = re.search(pattern, source, re.DOTALL)
    if not m:
        raise RuntimeError("buildTimingText not found in app.js")
    return m.group(0)


def _run_node(js_code):
    """Run JS code via node and return stdout."""
    result = subprocess.run(
        ["node", "-e", js_code],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


class TestBuildTimingTextNoDocumentFragment(unittest.TestCase):
    """Issue #198: buildTimingText must never return a DocumentFragment."""

    def test_no_documentfragment_in_source(self):
        """buildTimingText function body must not reference DocumentFragment."""
        source = _get_app_js()
        func_body = _extract_build_timing_text(source)
        self.assertNotIn(
            "createDocumentFragment", func_body,
            "buildTimingText must not use DocumentFragment (Issue #198)"
        )
        self.assertNotIn(
            "DocumentFragment", func_body,
            "buildTimingText must not reference DocumentFragment (Issue #198)"
        )

    def test_no_fragment_append_in_source(self):
        """buildTimingText must not build a frag and append children to it."""
        source = _get_app_js()
        func_body = _extract_build_timing_text(source)
        # These patterns indicate DocumentFragment building
        self.assertNotIn(
            "frag.appendChild", func_body,
            "buildTimingText must not use frag.appendChild (Issue #198)"
        )

    def test_returns_string_or_null_no_weemeta(self):
        """Without weeMeta, returns string or null — never object."""
        source = _get_app_js()
        func_body = _extract_build_timing_text(source)

        js = textwrap.dedent(f"""
            {func_body}

            const results = [
                [buildTimingText(2.5, null), typeof buildTimingText(2.5, null)],
                [buildTimingText(null, null), typeof buildTimingText(null, null)],
                [buildTimingText(0, null), typeof buildTimingText(0, null)],
            ];
            results.forEach(([val, t]) => {{
                if (val !== null && t !== 'string') {{
                    throw new Error(
                        'Expected string or null, got ' + t +
                        ': ' + String(val));
                }}
                // Detect DocumentFragment coercion
                if (String(val) === '[object DocumentFragment]') {{
                    throw new Error(
                        'Got [object DocumentFragment] for val: ' +
                        String(val));
                }}
            }});
            console.log('OK: ' + JSON.stringify(results.map(([v]) => v)));
        """)
        out, err, rc = _run_node(js)
        self.assertEqual(rc, 0, f"Node error: {err}")
        self.assertIn("OK:", out)
        # Verify the timing text shows correctly
        self.assertIn("2.5", out)

    def test_returns_string_copilot_sdk_runtime(self):
        """copilot-sdk runtime returns a plain string, not DocumentFragment."""
        source = _get_app_js()
        func_body = _extract_build_timing_text(source)

        js = textwrap.dedent(f"""
            {func_body}

            const weeMeta = {{ runtime: 'copilot-sdk' }};
            const r1 = buildTimingText(2.5, weeMeta);
            const r2 = buildTimingText(null, weeMeta);

            if (typeof r1 !== 'string') throw new Error(
                'Expected string, got: ' + typeof r1 + ' => ' + String(r1));
            if (typeof r2 !== 'string') throw new Error(
                'Expected string, got: ' + typeof r2 + ' => ' + String(r2));
            if (String(r1) === '[object DocumentFragment]') throw new Error(
                'Got DocumentFragment');
            if (String(r2) === '[object DocumentFragment]') throw new Error(
                'Got DocumentFragment');
            if (!r1.includes('copilot request')) throw new Error(
                'Missing copilot request in: ' + r1);
            if (!r1.includes('2.5')) throw new Error('Missing timing in: ' + r1);
            console.log('OK: r1=' + r1 + ' r2=' + r2);
        """)
        out, err, rc = _run_node(js)
        self.assertEqual(rc, 0, f"Node error: {err}")
        self.assertIn("OK:", out)

    def test_returns_string_with_tokens(self):
        """With token data returns a string containing token count."""
        source = _get_app_js()
        func_body = _extract_build_timing_text(source)

        js = textwrap.dedent(f"""
            {func_body}

            const weeMeta = {{
                tokens: 1200, prompt_tokens: 800,
                completion_tokens: 400,
            }};
            const r = buildTimingText(2.5, weeMeta);

            if (typeof r !== 'string') throw new Error(
                'Expected string, got: ' + typeof r + ' => ' + String(r));
            if (String(r) === '[object DocumentFragment]') throw new Error(
                'Got DocumentFragment');
            if (!r.includes('1,200')) throw new Error('Missing token count in: ' + r);
            console.log('OK: ' + r);
        """)
        out, err, rc = _run_node(js)
        self.assertEqual(rc, 0, f"Node error: {err}")
        self.assertIn("OK:", out)
        self.assertIn("1,200", out)

    def test_null_returned_when_no_data(self):
        """With no timing and no weeMeta, returns null."""
        source = _get_app_js()
        func_body = _extract_build_timing_text(source)

        js = textwrap.dedent(f"""
            {func_body}

            const r = buildTimingText(null, null);
            if (r !== null) throw new Error(
                'Expected null, got: ' + typeof r + ' => ' + String(r));
            console.log('OK: null');
        """)
        out, err, rc = _run_node(js)
        self.assertEqual(rc, 0, f"Node error: {err}")
        self.assertIn("OK:", out)

    def test_copilot_cost_label(self):
        """costLabel=copilot also returns a plain string."""
        source = _get_app_js()
        func_body = _extract_build_timing_text(source)

        js = textwrap.dedent(f"""
            {func_body}

            const weeMeta = {{ runtime: 'wee', cost_label: 'copilot' }};
            const r = buildTimingText(3.0, weeMeta);

            if (typeof r !== 'string') throw new Error(
                'Expected string, got: ' + typeof r + ' => ' + String(r));
            if (String(r) === '[object DocumentFragment]') throw new Error(
                'Got DocumentFragment');
            console.log('OK: ' + r);
        """)
        out, err, rc = _run_node(js)
        self.assertEqual(rc, 0, f"Node error: {err}")
        self.assertIn("OK:", out)

    def test_weemeta_no_tokens_with_timing(self):
        """weeMeta without tokens but with timing returns string."""
        source = _get_app_js()
        func_body = _extract_build_timing_text(source)

        js = textwrap.dedent(f"""
            {func_body}

            const weeMeta = {{ runtime: 'wee', cost_label: '' }};
            const r = buildTimingText(1.8, weeMeta);

            if (typeof r !== 'string') throw new Error(
                'Expected string, got: ' + typeof r + ' => ' + String(r));
            if (String(r) === '[object DocumentFragment]') throw new Error(
                'Got DocumentFragment');
            if (!r.includes('1.8')) throw new Error('Missing timing in: ' + String(r));
            console.log('OK: ' + r);
        """)
        out, err, rc = _run_node(js)
        self.assertEqual(rc, 0, f"Node error: {err}")
        self.assertIn("OK:", out)

    def test_weemeta_no_tokens_no_timing_returns_null(self):
        """weeMeta without tokens or timing returns null."""
        source = _get_app_js()
        func_body = _extract_build_timing_text(source)

        js = textwrap.dedent(f"""
            {func_body}

            const weeMeta = {{ runtime: 'wee', cost_label: '' }};
            const r = buildTimingText(null, weeMeta);

            if (r !== null) throw new Error(
                'Expected null, got: ' + typeof r + ' => ' + String(r));
            console.log('OK: null');
        """)
        out, err, rc = _run_node(js)
        self.assertEqual(rc, 0, f"Node error: {err}")
        self.assertIn("OK:", out)

    def test_innerHTML_usage_in_caller(self):
        """Callers use timingDiv.innerHTML to set the return value (string-safe)."""
        source = _get_app_js()
        # Check that the callers use innerHTML (string-based) approach
        timing_inner_html = re.findall(r"timingDiv\.innerHTML\s*=", source)
        self.assertGreaterEqual(
            len(timing_inner_html), 2,
            "Expected timingDiv.innerHTML assignments for timing divs"
        )


class TestBuildTimingTextOutputFormat(unittest.TestCase):
    """Verify the format of buildTimingText return values."""

    def _func_body(self):
        return _extract_build_timing_text(_get_app_js())

    def test_timing_format_with_seconds(self):
        """Timing text includes 'Generated in Xs' format."""
        func_body = self._func_body()
        js = textwrap.dedent(f"""
            {func_body}
            const r = buildTimingText(2.567, null);
            if (!r.includes('2.6s')) throw new Error('Bad format: ' + r);
            console.log('OK: ' + r);
        """)
        out, err, rc = _run_node(js)
        self.assertEqual(rc, 0, f"Node error: {err}")
        self.assertIn("2.6", out)

    def test_token_tooltip_format(self):
        """Tooltip shows input/output breakdown."""
        func_body = self._func_body()
        js = textwrap.dedent(f"""
            {func_body}
            const weeMeta = {{
                tokens: 500,
                prompt_tokens: 300,
                completion_tokens: 200,
            }};
            const r = buildTimingText(1.0, weeMeta);
            if (!r.includes('Input:')) throw new Error(
                'Missing Input: in tooltip: ' + r);
            if (!r.includes('Output:')) throw new Error(
                'Missing Output: in tooltip: ' + r);
            console.log('OK: ' + r.substring(0, 100));
        """)
        out, err, rc = _run_node(js)
        self.assertEqual(rc, 0, f"Node error: {err}")
        self.assertIn("OK:", out)


if __name__ == "__main__":
    unittest.main()
