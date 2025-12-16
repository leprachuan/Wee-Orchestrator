#!/usr/bin/env python3
"""Test script to verify agent delegation works correctly"""

import subprocess
import sys

def run_test(name, prompt, session_id):
    """Run a test with the agent_manager"""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    print(f"Prompt: {prompt}\n")
    
    try:
        result = subprocess.run(
            ['python3', 'agent_manager.py', prompt, session_id],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Show stdout only (not stderr which has session info)
        if result.stdout:
            print("OUTPUT:")
            print(result.stdout)
        
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("ERROR: Command timed out")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    tests = [
        ("Natural Language Auto-Delegation", 
         "ask the devops agent to check the ceph status on proxmox4",
         "test-nl-001"),
        
        ("Explicit Agent Invoke", 
         '/agent invoke devops "ssh into proxmox4 and check ceph status"',
         "test-invoke-001"),
    ]
    
    passed = 0
    failed = 0
    
    for name, prompt, session_id in tests:
        if run_test(name, prompt, session_id):
            passed += 1
        else:
            failed += 1
    
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")

