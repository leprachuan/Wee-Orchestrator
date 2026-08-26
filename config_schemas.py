"""
Pydantic schemas for Wee Orchestrator configuration files.

Validates agents.json, telegram_config.json, and webex_config.json.
Unknown keys generate warnings; missing required fields raise ValidationError.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _warn_unknown_keys(values: Any, known: set, context: str) -> None:
    """Emit a UserWarning for each key in *values* that is not in *known*."""
    if not isinstance(values, dict):
        return
    for key in values:
        if key not in known:
            warnings.warn(
                f"[config_schemas] {context}: unknown key '{key}' — "
                "possible typo, value will be ignored",
                UserWarning,
                stacklevel=5,
            )


# ---------------------------------------------------------------------------
# agents.json schemas
# ---------------------------------------------------------------------------

_BOT_CONFIG_KNOWN = {"token_secret", "allowed_users"}


class BotConfig(BaseModel):
    """Per-channel bot configuration inside an agent entry."""

    model_config = ConfigDict(extra="allow")

    token_secret: Optional[str] = None
    allowed_users: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _BOT_CONFIG_KNOWN, "agents[].bots.<channel>")
        return values


_AGENT_BOTS_CONFIG_KNOWN = {"telegram", "webex"}


class AgentBotsConfig(BaseModel):
    """Optional 'bots' block for an agent."""

    model_config = ConfigDict(extra="allow")

    telegram: Optional[BotConfig] = None
    webex: Optional[BotConfig] = None

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _AGENT_BOTS_CONFIG_KNOWN, "agents[].bots")
        return values


_DISPATCH_KNOWN = {
    "runtime",
    "model",
    "vendor",
    "permission_mode",
    "yolo",
    "timeout",
    "fallback_runtime",
    "fallback_model",
}


class AgentDispatchConfig(BaseModel):
    """Optional 'dispatch_config' specifying how to launch an agent."""

    model_config = ConfigDict(extra="allow")

    runtime: Optional[str] = None
    model: Optional[str] = None
    vendor: Optional[str] = None
    permission_mode: Optional[str] = None
    yolo: Optional[bool] = None
    timeout: Optional[int] = None
    fallback_runtime: Optional[str] = None
    fallback_model: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _DISPATCH_KNOWN, "dispatch_config")
        return values


_AGENT_KNOWN: set[str] = {
    "name",
    "description",
    "path",
    "max_concurrent",
    "primary_runtime",
    "primary_model",
    "fallback_runtime",
    "fallback_model",
    "permissions",
    "permission_mode",
    "yolo",
    "bots",
}


class AgentEntry(BaseModel):
    """A single entry in the 'agents' list of agents.json."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: Optional[str] = ""
    path: Optional[str] = ""
    max_concurrent: Optional[int] = 1
    primary_runtime: Optional[str] = None
    primary_model: Optional[str] = None
    fallback_runtime: Optional[str] = None
    fallback_model: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None
    permission_mode: Optional[str] = None
    yolo: Optional[bool] = None
    bots: Optional[AgentBotsConfig] = None

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        name = values.get("name", "?") if isinstance(values, dict) else "?"
        _warn_unknown_keys(values, _AGENT_KNOWN, f"agents['{name}']")
        return values


_AGENTS_CONFIG_KNOWN = {"agents"}


class AgentsConfig(BaseModel):
    """Top-level structure of agents.json."""

    model_config = ConfigDict(extra="allow")

    agents: List[AgentEntry]

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _AGENTS_CONFIG_KNOWN, "agents.json")
        return values


# ---------------------------------------------------------------------------
# telegram_config.json schema
# ---------------------------------------------------------------------------

_TELEGRAM_KNOWN = {
    "token",
    "allowed_users",
    "user_pairings",
    "enable_auto_pair",
    "default_agent",
    "default_model",
    "pinned_users",
    "yolo_allowed_users",
    "user_rate_limit_max_requests",
    "user_rate_limit_window_seconds",
}


class TelegramConfigSchema(BaseModel):
    """Schema for telegram_config.json."""

    model_config = ConfigDict(extra="allow")

    token: str = ""
    allowed_users: List[Union[int, str]] = Field(default_factory=list)
    user_pairings: Dict[str, Any] = Field(default_factory=dict)
    enable_auto_pair: bool = False
    default_agent: str = "orchestrator"
    default_model: str = "gpt-5-mini"
    pinned_users: Dict[str, Any] = Field(default_factory=dict)
    yolo_allowed_users: List[Union[int, str]] = Field(default_factory=list)
    user_rate_limit_max_requests: Optional[int] = None
    user_rate_limit_window_seconds: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _TELEGRAM_KNOWN, "telegram_config.json")
        return values


# ---------------------------------------------------------------------------
# webex_config.json schema
# ---------------------------------------------------------------------------

_WEBEX_KNOWN = {
    "token",
    "rabbitmq_host",
    "rabbitmq_port",
    "rabbitmq_user",
    "rabbitmq_password",
    "rabbitmq_queue",
    "rabbitmq_vhost",
    "rabbitmq_ssl",
    "rabbitmq_ssl_verify",
    "rabbitmq_host_ip",
    "rabbitmq_payload_key",
    "rabbitmq_queue_passive",
    "allowed_users",
    "user_pairings",
    "enable_auto_pair",
    "default_agent",
    "default_model",
    "pinned_users",
    "yolo_allowed_users",
    "user_rate_limit_max_requests",
    "user_rate_limit_window_seconds",
    "bot_token",
}


class WebEXConfigSchema(BaseModel):
    """Schema for webex_config.json."""

    model_config = ConfigDict(extra="allow")

    token: str = ""
    rabbitmq_host: str = "192.168.0.85"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "admin"
    rabbitmq_password: str = ""
    rabbitmq_queue: str = "webex"
    rabbitmq_vhost: str = "/"
    rabbitmq_ssl: Optional[bool] = None
    rabbitmq_ssl_verify: Optional[bool] = True
    rabbitmq_host_ip: Optional[str] = None
    rabbitmq_payload_key: Optional[str] = None
    rabbitmq_queue_passive: bool = False
    allowed_users: List[str] = Field(default_factory=list)
    user_pairings: Dict[str, Any] = Field(default_factory=dict)
    enable_auto_pair: bool = False
    default_agent: str = "orchestrator"
    default_model: str = "gpt-5-mini"
    pinned_users: Dict[str, Any] = Field(default_factory=dict)
    yolo_allowed_users: List[str] = Field(default_factory=list)
    user_rate_limit_max_requests: Optional[int] = None
    user_rate_limit_window_seconds: Optional[int] = None
    bot_token: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _WEBEX_KNOWN, "webex_config.json")
        return values


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def validate_agents_config(data: dict) -> AgentsConfig:
    """Validate agents.json data, warning on unknown keys.

    Raises pydantic.ValidationError on structural failures (e.g. missing
    required fields, wrong types). Unknown keys produce UserWarnings.

    Note: agents.json is intentionally excluded from CONNECTOR_VALIDATORS
    because it is loaded by agent_manager._load_agents_config(), not by
    BaseConfig._load_config(). It is called directly at load time in
    agent_manager.py instead.
    """
    return AgentsConfig.model_validate(data)


def validate_telegram_config(data: dict) -> TelegramConfigSchema:
    """Validate telegram_config.json data.

    Raises pydantic.ValidationError on structural failures. Unknown keys
    produce UserWarnings so typos are immediately visible.
    """
    return TelegramConfigSchema.model_validate(data)


def validate_webex_config(data: dict) -> WebEXConfigSchema:
    """Validate webex_config.json data.

    Raises pydantic.ValidationError on structural failures. Unknown keys
    produce UserWarnings so typos are immediately visible.
    """
    return WebEXConfigSchema.model_validate(data)


# ---------------------------------------------------------------------------
# router_config.json schema (issue #506 — LLM model router)
# ---------------------------------------------------------------------------

_ROUTER_BRAIN_KNOWN = {"runtime", "model"}


class RouterBrainConfig(BaseModel):
    """Runtime+model pair used to invoke the router's decision-making LLM."""

    model_config = ConfigDict(extra="allow")

    runtime: str
    model: str

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _ROUTER_BRAIN_KNOWN, "router_config.brain")
        return values

    @model_validator(mode="after")
    def no_recursion(self) -> "RouterBrainConfig":
        if self.runtime == "router":
            raise ValueError("brain.runtime cannot be 'router' (no recursive routing)")
        return self


_ROUTER_ALLOWLIST_ENTRY_KNOWN = {"runtime", "model", "hint"}


class RouterAllowlistEntry(BaseModel):
    """A single routable runtime+model target."""

    model_config = ConfigDict(extra="allow")

    runtime: str
    model: str
    hint: Optional[str] = ""

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _ROUTER_ALLOWLIST_ENTRY_KNOWN, "router_config.allowlist[]")
        return values

    @model_validator(mode="after")
    def no_recursion(self) -> "RouterAllowlistEntry":
        if self.runtime == "router":
            raise ValueError("allowlist entries cannot target 'router' itself")
        return self


_ROUTER_FALLBACK_KNOWN = {"runtime", "model"}


class RouterFallbackConfig(BaseModel):
    """Safe fallback pair used when the router brain fails, times out, or
    returns an invalid/disallowed decision."""

    model_config = ConfigDict(extra="allow")

    runtime: str
    model: str

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _ROUTER_FALLBACK_KNOWN, "router_config.fallback")
        return values

    @model_validator(mode="after")
    def no_recursion(self) -> "RouterFallbackConfig":
        if self.runtime == "router":
            raise ValueError("fallback.runtime cannot be 'router'")
        return self


_ROUTER_STICKINESS_KNOWN = {"enabled", "prefer_same_runtime", "window_seconds"}


class RouterStickinessConfig(BaseModel):
    """Preference for reusing the previously-routed runtime/model so the
    underlying runtime's own session (and its prompt cache) is reused."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    prefer_same_runtime: bool = True
    window_seconds: int = 900

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _ROUTER_STICKINESS_KNOWN, "router_config.stickiness")
        return values


_ROUTER_CONFIG_KNOWN = {
    "enabled",
    "brain",
    "timeout_seconds",
    "prompt_template",
    "allowlist",
    "fallback",
    "stickiness",
    "cooldown_seconds",
}

_REQUIRED_ROUTER_TEMPLATE_PLACEHOLDERS = ("{allowlist_table}", "{user_message}")


class RouterConfigSchema(BaseModel):
    """Schema for config/router_config.json (issue #506).

    This is the API-facing validator used by PUT /api/v1/router-config to
    return structured 422 errors. llm_router.RouterConfig.validate() runs the
    same checks independently as a defense-in-depth guard for callers that
    write the file directly (e.g. tests, manual edits) without going through
    the API.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    brain: RouterBrainConfig
    timeout_seconds: float = 30
    prompt_template: str
    allowlist: List[RouterAllowlistEntry]
    fallback: RouterFallbackConfig
    stickiness: RouterStickinessConfig = Field(default_factory=RouterStickinessConfig)
    cooldown_seconds: float = 300

    @model_validator(mode="before")
    @classmethod
    def warn_unknown(cls, values: Any) -> Any:
        _warn_unknown_keys(values, _ROUTER_CONFIG_KNOWN, "router_config.json")
        return values

    @model_validator(mode="after")
    def check_semantics(self) -> "RouterConfigSchema":
        if not self.allowlist:
            raise ValueError("allowlist must contain at least one runtime/model pair")
        for placeholder in _REQUIRED_ROUTER_TEMPLATE_PLACEHOLDERS:
            if placeholder not in self.prompt_template:
                raise ValueError(f"prompt_template missing required placeholder {placeholder}")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be a non-negative number")
        return self


def validate_router_config(data: dict) -> RouterConfigSchema:
    """Validate router_config.json data.

    Raises pydantic.ValidationError on structural or semantic failures
    (missing brain/allowlist/fallback, recursive 'router' targets, a
    prompt_template missing required placeholders, non-positive timeouts).
    Unknown keys produce UserWarnings.
    """
    return RouterConfigSchema.model_validate(data)


# ---------------------------------------------------------------------------
# Registry: maps config file basename -> validator function
# Used by base_connector.BaseConfig._load_config to auto-validate on load.
# ---------------------------------------------------------------------------

CONNECTOR_VALIDATORS = {
    "telegram_config.json": validate_telegram_config,
    "webex_config.json": validate_webex_config,
}
