"""
host_info.py — Detect runtime host and environment information.

This module ensures agents know exactly which host they're running on
and never attempt to SSH to themselves.
"""

import os
import socket
from typing import Tuple


def get_hostname() -> str:
    """Get the current system hostname."""
    return socket.gethostname().lower()


def get_environment() -> Tuple[str, str]:
    """
    Detect current environment (dev or prod) and hostname.
    
    Returns:
        Tuple of (environment, hostname)
        - environment: "dev" or "prod"
        - hostname: current system hostname
    """
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    hostname = get_hostname()
    
    # Auto-detect environment from repo dir name
    if repo_dir.endswith("-dev"):
        env = "dev"
    else:
        env = "prod"
    
    return env, hostname


def get_target_host(environment: str = None) -> str:
    """
    Get the target host for SSH operations based on environment.
    
    Args:
        environment: "dev" or "prod". If None, uses current environment.
    
    Returns:
        Hostname or IP to target for SSH
        - prod: "lepbuntu" or "100.124.186.75"
        - dev: "192.168.1.100"
    """
    if environment is None:
        environment, _ = get_environment()
    
    if environment == "dev":
        return "192.168.1.100"
    else:  # prod
        return "lepbuntu"


def should_ssh_to_host(target_host: str) -> bool:
    """
    Check if we should SSH to a target host.
    
    Returns False (don't SSH) if target_host matches our current hostname,
    to prevent SSH loops.
    
    Args:
        target_host: The hostname or IP to potentially SSH to
    
    Returns:
        True if safe to SSH, False if target is ourselves
    """
    current = get_hostname()
    target = target_host.lower().split(".")[0]  # Extract hostname without domain
    
    return current != target
