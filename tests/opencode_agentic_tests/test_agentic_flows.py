#!/usr/bin/env python3
"""
Comprehensive agentic flow tests for opencode/gemma4-26b
Tests: context persistence, tool calling, agentic flows, edge cases
"""

import json
import subprocess
import sys
import time
from datetime import datetime

class OpenCodeAgenticTester:
    def __init__(self):
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "model": "openai-compatible/gemma4-26b",
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "issues_filed": [],
            "failures": []
        }
    
    def run_opencode_test(self, test_name, prompt, turns=1, expect_tool_calls=False):
        """Run a single opencode test via agent_manager"""
        try:
            # Construct multi-turn prompt
            full_prompt = f"TEST: {test_name}\n\n{prompt}"
            
            # Run via agent_manager with opencode runtime
            cmd = [
                "python3", "/opt/n8n-copilot-shim/agent_manager.py",
                "--agent", "opencode",
                "--runtime", "opencode",
                "--model", "openai-compatible/gemma4-26b",
                "--config", "/opt/n8n-copilot-shim/agents.json",
                full_prompt,
                "test_session_opencode"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            self.results["tests_run"] += 1
            
            # Check if test passed
            if result.returncode == 0:
                self.results["tests_passed"] += 1
                return True, result.stdout
            else:
                self.results["tests_failed"] += 1
                self.results["failures"].append({
                    "test": test_name,
                    "error": result.stderr,
                    "stdout": result.stdout
                })
                return False, result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            self.results["tests_failed"] += 1
            self.results["tests_run"] += 1
            self.results["failures"].append({
                "test": test_name,
                "error": "Timeout after 60 seconds"
            })
            return False, "TIMEOUT"
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["tests_run"] += 1
            self.results["failures"].append({
                "test": test_name,
                "error": str(e)
            })
            return False, str(e)
    
    def test_context_persistence(self):
        """Test 1: Multi-turn context persistence (5+ turns)"""
        print("\n=== TEST 1: Context Persistence ===")
        
        # Multi-turn conversation that requires context accumulation
        prompt = """
Engage in a multi-turn conversation. Remember details from each turn:

Turn 1: I'm planning a research project about machine learning.
Turn 2: Specifically, I want to focus on deep learning for NLP.
Turn 3: The budget is $50,000.
Turn 4: I need 3 team members.
Turn 5: Timeline is 6 months.

After each turn, confirm what you've learned so far. At the end, summarize ALL details together.
This tests if you maintain context across 5+ turns without losing information.
"""
        
        success, output = self.run_opencode_test("context_persistence_5turns", prompt)
        
        # Check if all details are present in final response
        required_keywords = ["machine learning", "deep learning", "NLP", "$50,000", "3 team", "6 months"]
        all_present = all(kw.lower() in output.lower() for kw in required_keywords)
        
        if success and all_present:
            print("✓ PASS: Context persisted across 5 turns, all details retained")
            return True
        else:
            print(f"✗ FAIL: Context loss detected. Present: {all_present}")
            return False
    
    def test_tool_calling(self):
        """Test 2: Tool calling scenarios"""
        print("\n=== TEST 2: Tool Calling ===")
        
        tests = [
            ("simple_tool_call", "Use Python to calculate 12345 * 67890 and verify the result."),
            ("multi_step_tools", "Write code to: 1) create a list of numbers 1-100, 2) filter evens, 3) sum them."),
            ("conditional_logic", "Write code that reads a number and outputs 'even' or 'odd' based on value. Test with 42 and 17."),
        ]
        
        passed = 0
        for test_name, prompt in tests:
            success, output = self.run_opencode_test(test_name, prompt)
            if success:
                print(f"  ✓ {test_name}")
                passed += 1
            else:
                print(f"  ✗ {test_name}")
        
        return passed == len(tests)
    
    def test_agentic_flow(self):
        """Test 3: Multi-step agentic flow"""
        print("\n=== TEST 3: Agentic Flow (Research → Summarize → Format) ===")
        
        prompt = """
Complete this multi-step agent task without losing context:

Step 1 (Research): List 5 key concepts about quantum computing
Step 2 (Summarize): Create a 2-sentence summary of quantum computing basics
Step 3 (Format): Present the summary as a JSON object with fields: concept, summary, difficulty_level

Verify that all steps reference information from step 1.
"""
        
        success, output = self.run_opencode_test("agentic_flow_research", prompt)
        
        # Check for all three steps completed
        has_step1 = "quantum" in output.lower() and len(output) > 200
        has_step3 = "json" in output.lower() or "{" in output
        
        if success and has_step1 and has_step3:
            print("✓ PASS: Multi-step agentic flow completed")
            return True
        else:
            print(f"✗ FAIL: Agentic flow incomplete (step1:{has_step1}, step3:{has_step3})")
            return False
    
    def test_edge_cases(self):
        """Test 4: Edge cases"""
        print("\n=== TEST 4: Edge Cases ===")
        
        tests = [
            ("long_context", "Generate a 2000-token context window test. " + "Describe the importance of context in LLMs. " * 50),
            ("rapid_tool_calls", "Write 5 independent Python snippets: 1) factorial(10), 2) fibonacci(10), 3) is_prime(17), 4) gcd(48,18), 5) lcm(12,15)"),
            ("error_recovery", "Attempt to execute invalid Python code first, then correct it and show the fixed version."),
        ]
        
        passed = 0
        for test_name, prompt in tests:
            success, output = self.run_opencode_test(test_name, prompt)
            if success:
                print(f"  ✓ {test_name}")
                passed += 1
            else:
                print(f"  ✗ {test_name}")
        
        return passed == len(tests)
    
    def file_github_issue(self, title, body, labels):
        """File a GitHub issue in leprachuan/Wee-Orchestrator"""
        try:
            cmd = [
                "gh", "issue", "create",
                "--repo", "leprachuan/Wee-Orchestrator",
                "--title", title,
                "--body", body,
                "--label", ",".join(labels)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                # Extract issue number from output
                issue_output = result.stdout.strip()
                return issue_output
            else:
                print(f"Error filing issue: {result.stderr}")
                return None
        except Exception as e:
            print(f"Error: {e}")
            return None
    
    def run_all_tests(self):
        """Run comprehensive test suite"""
        print(f"\n{'='*60}")
        print(f"OpenCode Agentic Flow Test Suite")
        print(f"Model: gemma4-26b")
        print(f"Started: {self.results['timestamp']}")
        print(f"{'='*60}")
        
        results = []
        
        # Run all test categories
        results.append(("Context Persistence", self.test_context_persistence()))
        results.append(("Tool Calling", self.test_tool_calling()))
        results.append(("Agentic Flow", self.test_agentic_flow()))
        results.append(("Edge Cases", self.test_edge_cases()))
        
        # File issues for failures
        if self.results["failures"]:
            print(f"\n=== Filing GitHub Issues for {len(self.results['failures'])} failures ===")
            for failure in self.results["failures"]:
                title = f"Gemma4-26B: {failure['test']} failed"
                body = f"""
Test: {failure['test']}
Timestamp: {self.results['timestamp']}

## Failure Details
```
{failure.get('error', 'Unknown error')}
```

## Output
```
{failure.get('stdout', '')[:500]}
```

## To Reproduce
Run comprehensive opencode agentic flow tests with model `openai-compatible/gemma4-26b`
"""
                issue = self.file_github_issue(title, body, ["bug", "opencode-runtime"])
                if issue:
                    self.results["issues_filed"].append(issue)
                    print(f"  Filed: {issue}")
        
        # Print summary
        print(f"\n{'='*60}")
        print(f"TEST SUMMARY")
        print(f"{'='*60}")
        print(f"Tests Run:    {self.results['tests_run']}")
        print(f"Tests Passed: {self.results['tests_passed']}")
        print(f"Tests Failed: {self.results['tests_failed']}")
        print(f"Issues Filed: {len(self.results['issues_filed'])}")
        print(f"\nTest Results:")
        for test_name, passed in results:
            status = "✓ PASS" if passed else "✗ FAIL"
            print(f"  {status}: {test_name}")
        
        if self.results["issues_filed"]:
            print(f"\nGitHub Issues Filed:")
            for issue in self.results["issues_filed"]:
                print(f"  {issue}")
        
        print(f"{'='*60}\n")
        
        return self.results

if __name__ == "__main__":
    tester = OpenCodeAgenticTester()
    results = tester.run_all_tests()
    
    # Output results as JSON
    print("\n--- JSON Results ---")
    print(json.dumps(results, indent=2))

