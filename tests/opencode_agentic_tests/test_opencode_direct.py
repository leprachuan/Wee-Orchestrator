#!/usr/bin/env python3
"""
Agentic flow tests using opencode runtime directly via curl API
Tests with model: openai-compatible/gemma4-26b
"""

import json
import subprocess
from datetime import datetime


class OpenCodeDirectTester:
    def __init__(self):
        self.api_url = "https://192.168.1.100:8001"
        self.auth_token = "shared_R6R6wReORUV6bouLntScMTowbsh30Rzqa3hzjs3bWgU"
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "model": "openai-compatible/gemma4-26b",
            "runtime": "opencode",
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "issues_filed": [],
            "failures": [],
        }

    def run_opencode_api_test(self, test_name, prompt, timeout=60):
        """Run test via opencode API directly"""
        try:
            payload = {
                "prompt": f"TEST: {test_name}\n\n{prompt}",
                "runtime": "opencode",
                "model": "openai-compatible/gemma4-26b",
                "timeout": timeout,
            }

            cmd = [
                "curl",
                "-k",
                "-X",
                "POST",
                f"{self.api_url}/api/v1/query",
                "-H",
                "Content-Type: application/json",
                "-H",
                f"Authorization: Bearer {self.auth_token}",
                "-H",
                "X-User-Identity: wee-dev-test",
                "-H",
                "X-Auth-Channel: api",
                "-d",
                json.dumps(payload),
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 10
            )
            self.results["tests_run"] += 1

            if result.returncode == 0:
                try:
                    response = json.loads(result.stdout)
                    if "response" in response or "output" in response:
                        self.results["tests_passed"] += 1
                        output = response.get("response", response.get("output", ""))
                        return True, output
                except Exception:  # noqa: B014
                    pass

            self.results["tests_failed"] += 1
            self.results["failures"].append(
                {
                    "test": test_name,
                    "error": result.stderr,
                    "stdout": result.stdout[:500],
                }
            )
            return False, result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            self.results["tests_failed"] += 1
            self.results["tests_run"] += 1
            self.results["failures"].append({"test": test_name, "error": "Timeout"})
            return False, "TIMEOUT"
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["tests_run"] += 1
            self.results["failures"].append({"test": test_name, "error": str(e)})
            return False, str(e)

    def test_context_persistence(self):
        """Test multi-turn context"""
        print("\n=== TEST 1: Context Persistence ===")
        prompt = """
Hold context across this multi-turn conversation:
1. Subject: Machine Learning research project
2. Focus: Deep Learning for NLP
3. Budget: $50,000
4. Team size: 3 members
5. Timeline: 6 months

After processing all items, confirm you remember ALL five details."""

        success, output = self.run_opencode_api_test(
            "context_multi_turn", prompt, timeout=45
        )

        required = [
            "machine learning",
            "deep learning",
            "nlp",
            "$50,000",
            "3",
            "6 months",
        ]
        all_present = all(kw.lower() in output.lower() for kw in required)

        if success and all_present:
            print("✓ PASS: Multi-turn context retained")
            return True
        else:
            print(f"✗ FAIL: Context incomplete. Details present: {all_present}")
            if not success:
                print(f"  Error: {output[:200]}")
            return False

    def test_tool_calling(self):
        """Test tool calling patterns"""
        print("\n=== TEST 2: Tool Calling ===")

        tests = [
            ("calc", "Calculate: (123 * 456) + 789 - 45. Show steps and final result."),
            (
                "code_gen",
                "Write Python to: 1) create list [1..100], 2) filter for primes, 3) sum them.",  # noqa: E501
            ),
            (
                "logic",
                "If x=7, is it prime? If y=12, what's gcd(y,18)? Show both calculations.",  # noqa: E501
            ),
        ]

        passed = 0
        for name, prompt in tests:
            success, output = self.run_opencode_api_test(name, prompt, timeout=45)
            if success and len(output) > 50:
                print(f"  ✓ {name}")
                passed += 1
            else:
                print(f"  ✗ {name}")

        return passed >= 2

    def test_agentic_flow(self):
        """Test multi-step flow"""
        print("\n=== TEST 3: Agentic Flow ===")

        prompt = """
Complete this 3-step task, maintaining state between steps:
Step 1: Define quantum computing principles (3-5 key concepts)
Step 2: Summarize into 1 sentence
Step 3: Format as JSON with {concept, summary, key_benefit}

Show all three steps and verify step 3 uses info from step 1."""

        success, output = self.run_opencode_api_test(
            "flow_multi_step", prompt, timeout=45
        )

        has_concepts = "quantum" in output.lower() or "superposition" in output.lower()
        has_json = "{" in output and "}" in output

        if success and has_concepts and has_json:
            print("✓ PASS: Multi-step flow maintained state")
            return True
        else:
            print(f"✗ FAIL: Flow incomplete (concepts:{has_concepts}, json:{has_json})")
            return False

    def test_edge_cases(self):
        """Test edge cases"""
        print("\n=== TEST 4: Edge Cases ===")

        tests = [
            (
                "long",
                "Discuss LLM context windows. " * 40 + " Summarize in 2 sentences.",
            ),
            (
                "rapid",
                "Compute: factorial(5), fibonacci(8), prime_check(17), gcd(48,18), lcm(12,15)",  # noqa: E501
            ),
            (
                "error_fix",
                "First, try: print(1/0). Then fix it to: result = 1/2; print(result)",
            ),
        ]

        passed = 0
        for name, prompt in tests:
            success, output = self.run_opencode_api_test(name, prompt, timeout=45)
            if success and len(output) > 30:
                print(f"  ✓ {name}")
                passed += 1
            else:
                print(f"  ✗ {name}")

        return passed >= 2

    def file_github_issue(self, title, body, labels):
        """File issue without invalid labels"""
        try:
            cmd = [
                "gh",
                "issue",
                "create",
                "--repo",
                "leprachuan/Wee-Orchestrator",
                "--title",
                title,
                "--body",
                body,
            ]
            # Add only valid labels
            valid_labels = [label for label in labels if label in ["bug", "wee-dev"]]
            if valid_labels:
                cmd.extend(["--label", ",".join(valid_labels)])

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception:  # noqa: B014
            return None

    def run_all_tests(self):
        """Run all tests"""
        print(f"\n{'='*70}")
        print("OpenCode Agentic Flow Tests")
        print("Runtime: opencode | Model: gemma4-26b")
        print(f"API: {self.api_url}")
        print(f"Started: {self.results['timestamp']}")
        print(f"{'='*70}")

        results = []
        results.append(("Context Persistence", self.test_context_persistence()))
        results.append(("Tool Calling", self.test_tool_calling()))
        results.append(("Agentic Flow", self.test_agentic_flow()))
        results.append(("Edge Cases", self.test_edge_cases()))

        # File issues for any failures
        for failure in self.results["failures"][:3]:  # Limit to 3 issues
            title = f"OpenCode/Gemma4: {failure['test']} test failed"
            body = """## Test Failure

Test: `{failure['test']}`
Runtime: opencode
Model: openai-compatible/gemma4-26b
Timestamp: {self.results['timestamp']}

### Error
```
{failure.get('error', 'Unknown')[:300]}
```

### Steps to Reproduce
1. Run opencode agentic flow test suite
2. Check test: {failure['test']}
3. Observe failure
"""
            issue = self.file_github_issue(title, body, ["bug", "wee-dev"])
            if issue:
                self.results["issues_filed"].append(issue)

        # Summary
        print(f"\n{'='*70}")
        print("SUMMARY")
        print(f"{'='*70}")
        print(f"Tests Run:      {self.results['tests_run']}")
        print(f"Tests Passed:   {self.results['tests_passed']}")
        print(f"Tests Failed:   {self.results['tests_failed']}")
        print(f"Issues Filed:   {len(self.results['issues_filed'])}")
        print("\nResults by Category:")
        for name, passed in results:
            status = "✓ PASS" if passed else "✗ FAIL"
            print(f"  {status}: {name}")

        if self.results["issues_filed"]:
            print("\nGitHub Issues:")
            for issue in self.results["issues_filed"]:
                print(f"  - {issue}")

        print(f"{'='*70}\n")
        return self.results


if __name__ == "__main__":
    tester = OpenCodeDirectTester()
    results = tester.run_all_tests()
