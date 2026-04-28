"""Async API client for Wee Orchestrator"""
import httpx
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

class WeeAPIClient:
    """Async client for Wee Orchestrator API"""
    
    def __init__(self, base_url: str, auth_token: str, user_id: str, channel: str = "tui", verify_ssl: bool = False):
        self.base_url = base_url
        self.auth_token = auth_token
        self.user_id = user_id
        self.channel = channel
        self.verify_ssl = verify_ssl
        self.headers = {
            "Authorization": f"Bearer {auth_token}",
            "X-User-Identity": user_id,
            "X-Auth-Channel": channel,
            "Content-Type": "application/json",
        }
        self.client: Optional[httpx.AsyncClient] = None
        
    async def __aenter__(self):
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            verify=self.verify_ssl,
            timeout=30.0,
        )
        return self
        
    async def __aexit__(self, *args):
        if self.client:
            await self.client.aclose()
    
    async def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Make an API request"""
        if not self.client:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}
        except httpx.HTTPError as e:
            logger.error(f"API error: {e}")
            raise
    
    # Session endpoints
    async def get_sessions(self) -> List[Dict[str, Any]]:
        """Get all sessions"""
        data = await self._request("GET", "/api/v1/history/sessions")
        return data.get("sessions", [])
    
    async def get_session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Get messages for a session"""
        data = await self._request("GET", f"/api/v1/history/sessions/{session_id}/messages")
        return data.get("messages", [])
    
    async def create_session(self, runtime: str, model: str, agent: str = "orchestrator") -> str:
        """Create a new session"""
        data = await self._request("POST", "/api/v1/sessions/create", json={
            "runtime": runtime,
            "model": model,
            "agent": agent,
        })
        return data.get("session_id", "")
    
    async def stream_session(self, session_id: str, prompt: str) -> str:
        """Stream a session execution"""
        data = await self._request("POST", f"/api/v1/sessions/{session_id}/stream", json={
            "prompt": prompt,
        })
        return data.get("result", "")
    
    async def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get session status"""
        return await self._request("GET", f"/api/v1/sessions/{session_id}/status")
    
    # Background task endpoints
    async def get_background_tasks(self) -> List[Dict[str, Any]]:
        """Get all background tasks"""
        data = await self._request("GET", "/api/v1/background-tasks")
        return data.get("tasks", [])
    
    async def get_background_task(self, task_id: str) -> Dict[str, Any]:
        """Get a specific background task"""
        return await self._request("GET", f"/api/v1/background-tasks/{task_id}")
    
    async def create_background_task(self, prompt: str, agent: str, runtime: str, model: str) -> str:
        """Create a background task"""
        data = await self._request("POST", "/api/v1/background-tasks", json={
            "prompt": prompt,
            "agent": agent,
            "runtime": runtime,
            "model": model,
        })
        return data.get("task_id", "")
    
    # Config endpoints
    async def get_agents(self) -> List[Dict[str, Any]]:
        """Get all agents"""
        data = await self._request("GET", "/api/v1/agents")
        return data.get("agents", [])
    
    async def get_runtimes(self) -> List[Dict[str, Any]]:
        """Get available runtimes"""
        data = await self._request("GET", "/api/v1/runtimes")
        return data.get("runtimes", [])
    
    async def get_models(self) -> List[str]:
        """Get available models"""
        data = await self._request("GET", "/api/v1/models")
        return data.get("models", [])
    
    async def get_service_status(self) -> Dict[str, Any]:
        """Get service status"""
        data = await self._request("GET", "/api/v1/service-status")
        return data.get("services", [])
    
    async def get_health(self) -> Dict[str, Any]:
        """Get API health"""
        return await self._request("GET", "/api/v1/health")
