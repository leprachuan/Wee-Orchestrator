#!/usr/bin/env python3
"""Final comprehensive opencode agentic tests with gemma4-26b"""
import subprocess
import json
import time
from datetime import datetime

class OpenCodeAgenticTest:
    def __init__(self):
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "model": "gemma4-26b",
            "runtime": "opencode",
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "failures": []
        }
    
    def run_opencode_test(self, name, prompt, timeout=30):
        """Run test with opencode"""
        try:
            self.results["tests_run"] += 1
            
            # Use agent_manager directly via subprocess
            cmd = [
                "python3", "/opt/n8n-copilot-shim-dev/agent_manager.py",
                "--runtime", "opencode",
                "--model", "openai-compatible/gemma4-26b",
                name,
                "test_session_oc"
            ]
            
            # Pass prompt via stdin or as positional arg
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            output = result.stdout + result.stderr
            
            if result.returncode == 0 and len(output.strip()) > 20:
                self.results["tests_passed"] += 1
                return True, output[:500]
            else:
                self.results["tests_failed"] += 1
                self.results["failures"].append({
                    "test": name,
                    "return_code": result.returncode,
                    "output": output[:300]
                })
                return False, output[:300]
        except subprocess.TimeoutExpired:
            self.results["tests_failed"] += 1
            self.results["failures"].append({
                "test": name,
                "error": "Timeout"
            })
            return False, "TIMEOUT"
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["failures"].append({
                "test": name,
                "error": str(e)
            })
            return False, str(e)
    
    def run_tests(self):
        """Run all test categories"""
        print("\n" + "="*70)
        print("OPENCODE AGENTIC FLOW TEST SUITE")
        print(f"Model: {self.results['model']}")
        print(f"Runtime: {self.results['runtime']}")
        print(f"Started: {self.results['timestamp']}")
        print("="*70 + "\n")
        
        # Test 1: Context Persistence
        print("[1/4] Testing Context Persistence (5+ turns)...")
        ctx_prompt = """Maintain context across 5 sequential inputs:
1. Project: ML Research
2. Focus: Deep Learning + NLP
3. Budget: $50,000
4. Team: 3 people
5. Timeline: 6 months
At the end, list all 5 items you remembered."""
        
        ctx_pass, ctx_out = self.run_opencode_test("context_persistence", ctx_prompt, timeout=45)
        ctx_success = ctx_pass and all(
            kw.lower() in ctx_out.lower()
            for kw in ["project", "ml", "deep", "budget", "timeline"]
        )
        print(f"  Result: {'✓ PASS' if ctx_success else '✗ FAIL'}")
        
        # Test 2: Tool Calling
        print("[2/4] Testing Tool Calling (multi-step)...")
        tool_prompts = [
            ("calc", "Compute (123 * 456) + 789 - 45. Show work."),
            ("code", "Write Python: sum all primes from 2 to 50."),
            ("logic", "If x=7 is prime? If y=48, find gcd(y,18) and lcm(y,18).")
        ]
        
        tool_passed = 0
        for tname, tprompt in tool_prompts:
            t_pass, _ = self.run_opencode_test(f"tool_{tname}", tprompt, timeout=45)
            if t_pass:
                tool_passed += 1
                print(f"    ✓ {tname}")
            else:
                print(f"    ✗ {tname}")
        
        tool_success = tool_passed >= 2
        print(f"  Result: {'✓ PASS' if tool_success else '✗ FAIL'} ({tool_passed}/3)")
        
        # Test 3: Agentic Flow
        print("[3/4] Testing Agentic Flow (multi-step with state)...")
        flow_prompt = """Complete this 3-step task:
STEP 1: Define 3 quantum computing concepts
STEP 2: Write a 1-sentence summary referencing step 1
STEP 3: Output as JSON {concept, summary, application}
Ensure step 3 uses info from step 1."""
        
        flow_pass, flow_out = self.run_opencode_test("agentic_flow", flow_prompt, timeout=45)
        flow_success = flow_pass and ("{" in flow_out and "}" in flow_out)
        print(f"  Result: {'✓ PASS' if flow_success else '✗ FAIL'}")
        
        # Test 4: Edge Cases
        print("[4/4] Testing Edge Cases...")
        edge_prompts = [
            ("long_ctx", "Discuss LLM context windows extensively. " * 20 + " Summarize in 1 sentence."),
            ("rapid", "Compute: 5! + fib(8) + is_prime(17) + gcd(48,18) + lcm(12,15)"),
            ("error", "Try dividing by zero, then fix it. Show both."),
        ]
        
        edge_passed = 0
        for ename, eprompt in edge_prompts:
            e_pass, _ = self.run_opencode_test(f"edge_{ename}", eprompt, timeout=45)
            if e_pass:
                edge_passed += 1
                print(f"    ✓ {ename}")
            else:
                print(f"    ✗ {ename}")
        
        edge_success = edge_passed >= 2
        print(f"  Result: {'✓ PASS' if edge_success else '✗ FAIL'} ({edge_passed}/3)")
        
        # Summary
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        print(f"Total Tests Run:    {self.results['tests_run']}")
        print(f"Tests Passed:       {self.results['tests_passed']}")
        print(f"Tests Failed:       {self.results['tests_failed']}")
        print(f"\nBy Category:")
        print(f"  Context Persistence: {'✓ PASS' if ctx_success else '✗ FAIL'}")
        print(f"  Tool Calling:        {'✓ PASS' if tool_success else '✗ FAIL'} ({tool_passed}/3)")
        print(f"  Agentic Flow:        {'✓ PASS' if flow_success else '✗ FAIL'}")
        print(f"  Edge Cases:          {'✓ PASS' if edge_success else '✗ FAIL'} ({edge_passed}/3)")
        
        all_pass = [ctx_success, tool_success, flow_success, edge_success]
        pass_count = sum(all_pass)
        print(f"\nOverall: {pass_count}/4 categories passed")
        
        if self.results["failures"]:
            print(f"\nFailures ({len(self.results['failures'])}):")
            for f in self.results["failures"][:5]:
                print(f"  - {f.get('test', 'unknown')}: {f.get('error', f.get('output', 'unknown'))[:80]}")
        
        print("="*70 + "\n")
        
        return {
            "summary": f"{pass_count}/4 test categories passed",
            "total_tests": self.results["tests_run"],
            "passed": self.results["tests_passed"],
            "failed": self.results["tests_failed"]
        }

if __name__ == "__main__":
    tester = OpenCodeAgenticTest()
    result = tester.run_tests()
    print("\nFinal Result:", json.dumps(result, indent=2))

