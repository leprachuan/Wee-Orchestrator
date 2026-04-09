#!/usr/bin/env python3
"""Test opencode runtime CLI directly"""
import subprocess
import json
import time
from datetime import datetime

class OpenCodeCLITester:
    def __init__(self):
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "failures": []
        }
    
    def test_via_cli(self, test_name, prompt):
        """Run test via opencode CLI"""
        try:
            # Try to find opencode agent binary
            cmd = ["which", "opencode"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                # Try finding agent binary
                cmd = ["find", "/usr/local/bin", "/opt", "-name", "agent", "-type", "f"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                agent_path = result.stdout.strip().split('\n')[0] if result.stdout else None
                
                if not agent_path:
                    return False, "OpenCode agent not found"
            
            self.results["tests_run"] += 1
            
            # Run test
            cmd = ["timeout", "30", "python3", "-c", f"""
import subprocess
result = subprocess.run(['opencode', '--model', 'auto'], input='{prompt}', capture_output=True, text=True, timeout=25)
print(result.stdout)
print(result.stderr, file=__import__('sys').stderr)
"""]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
            
            if result.returncode == 0 and len(result.stdout) > 20:
                self.results["tests_passed"] += 1
                return True, result.stdout
            else:
                self.results["tests_failed"] += 1
                self.results["failures"].append({
                    "test": test_name,
                    "error": result.stderr[:200],
                    "output": result.stdout[:200]
                })
                return False, result.stderr
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["tests_run"] += 1
            self.results["failures"].append({
                "test": test_name,
                "error": str(e)
            })
            return False, str(e)
    
    def check_opencode_available(self):
        """Check if opencode runtime is available"""
        try:
            result = subprocess.run(["which", "opencode"], capture_output=True, timeout=5)
            return result.returncode == 0
        except:
            return False

if __name__ == "__main__":
    tester = OpenCodeCLITester()
    
    print("Checking if opencode is available...")
    available = tester.check_opencode_available()
    print(f"OpenCode available: {available}")
    
    if not available:
        print("\nOpenCode binary not found in PATH. Checking agent installation...")
        result = subprocess.run(["ls", "-la", "/usr/local/bin/"], capture_output=True, text=True)
        print(result.stdout[:500])
        
        print("\nChecking /opt for agent...")
        result = subprocess.run(["find", "/opt", "-name", "agent", "-type", "f", "-executable"], 
                              capture_output=True, text=True, timeout=5)
        if result.stdout:
            print("Found agent at:", result.stdout.split('\n')[0])
        else:
            print("No agent binary found in /opt")

