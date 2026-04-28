"""Shared data types for wee-tui"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional


@dataclass
class Agent:
    """Agent configuration"""

    name: str
    description: str
    primary_runtime: str
    primary_model: str
    color: str = "white"


@dataclass
class Session:
    """Wee Orchestrator session"""

    id: str
    runtime: str
    model: str
    agent: str
    status: str  # "running", "idle", "completed"
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


@dataclass
class Message:
    """Chat message"""

    id: str
    role: str  # "user", "assistant"
    content: str
    timestamp: datetime
    tool_calls: List[dict] = field(default_factory=list)


@dataclass
class BackgroundTask:
    """Background task"""

    id: str
    prompt: str
    agent: str
    runtime: str
    model: str
    status: str  # "queued", "running", "completed", "failed"
    created_at: datetime
    updated_at: datetime
    output: Optional[str] = None
    progress: float = 0.0


@dataclass
class ServiceStatus:
    """Service status"""

    name: str
    status: str  # "running", "stopped", "error"
    uptime_seconds: Optional[float] = None
    error_message: Optional[str] = None


@dataclass
class RuntimeInfo:
    """Runtime information"""

    name: str
    available: bool
    usage_percent: Optional[float] = None
    models: List[str] = field(default_factory=list)
