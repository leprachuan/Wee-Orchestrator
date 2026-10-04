"""Fail-closed policy primitives; execution wiring is intentionally separate.

Only trusted server adapters may construct Actions. A model-supplied label is
not proof that a shell command is a structured operation. No runtime imports
this module until the approval service can gate every autonomous tool path.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import threading
from uuid import uuid4

_DECISIONS = {"allow", "ask", "deny"}
_OPAQUE = {"shell.execute", "python.execute", "browser.execute", "delegate.execute"}
_KNOWN = _OPAQUE | {"file.read", "file.write", "service.restart", "repository.read",
                    "repository.modify", "message.send", "release.deploy", "model.escalate"}


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or any(ord(c) < 32 for c in value):
        raise ValueError("Expected a nonempty bounded string without control characters")
    return value


def _path(value):
    _text(value)
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("Path scope must be absolute without traversal")
    return str(path)


@dataclass(frozen=True)
class Action:
    agent: str
    operation: str
    host: str
    resource: str
    # Canonical JSON snapshot avoids mutable arguments changing after review.
    arguments_json: str = "{}"

    def __post_init__(self):
        for value in (self.agent, self.operation, self.host, self.resource):
            _text(value)
        arguments = json.loads(self.arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("Action arguments must be an object")
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(canonical) > 65536:
            raise ValueError("Action arguments exceed limit")
        object.__setattr__(self, "arguments_json", canonical)
        if self.operation.startswith("file."):
            object.__setattr__(self, "resource", _path(self.resource))

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Rule:
    id: str
    agent: str
    operation: str
    host: str
    resource: str
    decision: str
    created_by: str
    created_at: str
    source_approval_id: str
    enabled: bool = True
    path_prefix: bool = False
    revoked_by: str | None = None
    revoked_at: str | None = None

    def __post_init__(self):
        for value in (self.id, self.agent, self.operation, self.host, self.resource,
                      self.created_by, self.created_at, self.source_approval_id):
            _text(value)
        if self.decision not in _DECISIONS or type(self.enabled) is not bool or type(self.path_prefix) is not bool:
            raise ValueError("Invalid rule decision or flags")
        # Literal identifiers only: no implicit broad grants from '*' or regex.
        if any('*' in value for value in (self.agent, self.operation, self.host, self.resource)):
            raise ValueError("Wildcard scopes are not supported")
        if self.path_prefix:
            if self.operation not in {"file.read", "file.write"}:
                raise ValueError("Path prefixes are only supported for file operations")
            object.__setattr__(self, "resource", _path(self.resource))
        if self.operation.startswith("file."):
            object.__setattr__(self, "resource", _path(self.resource))
        if self.revoked_by is not None:
            _text(self.revoked_by)
        if self.revoked_at is not None:
            _text(self.revoked_at)
        if (self.revoked_by is None) != (self.revoked_at is None) or (self.revoked_at and self.enabled):
            raise ValueError("Revocation must include actor/time and disable the rule")

    def matches(self, action):
        if not self.enabled or (self.agent, self.operation, self.host) != (action.agent, action.operation, action.host):
            return False
        if self.path_prefix:
            return PurePosixPath(action.resource).is_relative_to(PurePosixPath(self.resource))
        return self.resource == action.resource


def evaluate(action, rules, *, enabled=False):
    """Return (decision, reason); grants cannot override a deny or opaque tool.

    File paths are lexical scopes; execution adapters must resolve symlinks and
    enforce the same scope at use time. The feature flag never bypasses checks.
    """
    if type(enabled) is not bool or not enabled:
        return "deny", "autonomy_disabled"
    matches = [rule for rule in rules if rule.matches(action)]
    if any(rule.decision == "deny" for rule in matches):
        return "deny", "matching_deny_rule"
    if action.operation not in _KNOWN or action.operation in _OPAQUE:
        return "ask", "unclassified_or_opaque_action"
    if any(rule.decision == "ask" for rule in matches):
        return "ask", "matching_approval_rule"
    if any(rule.decision == "allow" for rule in matches):
        return "allow", "matching_allow_rule"
    return "ask", "no_matching_rule"


class PolicyStore:
    """Single API-process writer. Multi-process locking is required before use.

    Read errors propagate so malformed policies cannot silently become grants.
    Action arguments/credentials are never persisted in the rule file.
    """
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self):
        with self._lock:
            if not self.path.exists():
                return {"version": 1, "revision": 0, "enabled": False, "rules": []}
            data = json.loads(self.path.read_text())
            if set(data) != {"version", "revision", "enabled", "rules"} or type(data['version']) is not int or data['version'] != 1:
                raise ValueError("Unsupported policy schema")
            if type(data['revision']) is not int or data['revision'] < 0 or type(data['enabled']) is not bool:
                raise ValueError("Invalid policy revision/flag")
            if not isinstance(data['rules'], list) or len(data['rules']) > 1000:
                raise ValueError("Invalid policy rules")
            rules = [Rule(**item) for item in data['rules']]
            if len({rule.id for rule in rules}) != len(rules):
                raise ValueError("Duplicate policy rule IDs")
            return {**data, "rules": rules}

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as handle:
                temporary = handle.name
                json.dump({**data, "rules": [asdict(rule) for rule in data['rules']]}, handle, indent=2)
                handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def add(self, *, actor, approval_id, agent, operation, host, resource, decision, path_prefix=False):
        with self._lock:
            data = self.load()
            if len(data['rules']) >= 1000:
                raise ValueError("Policy rule limit reached")
            rule = Rule(str(uuid4()), agent, operation, host, resource, decision,
                        _text(actor), datetime.now(timezone.utc).isoformat(), _text(approval_id), path_prefix=path_prefix)
            data['rules'].append(rule); data['revision'] += 1
            self._save(data)
            return rule

    def revoke(self, rule_id, *, actor):
        with self._lock:
            data = self.load()
            for index, rule in enumerate(data['rules']):
                if rule.id == rule_id:
                    if not rule.enabled:
                        return rule
                    fields = asdict(rule)
                    fields.update(enabled=False, revoked_by=_text(actor), revoked_at=datetime.now(timezone.utc).isoformat())
                    updated = Rule(**fields)
                    data['rules'][index] = updated; data['revision'] += 1
                    self._save(data)
                    return updated
            raise KeyError(rule_id)
