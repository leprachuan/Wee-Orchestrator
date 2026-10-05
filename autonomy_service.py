"""Authenticated shared approvals and the sole boundary for Always-On adapters.

Ordinary chat runtimes are separate. Always-On workers may only call registered
structured adapters here; shell, browser, SDK built-ins and delegation are not
registered and cannot escape this boundary.
"""

from dataclasses import asdict
import json
from pathlib import Path
from autonomy_approvals import ApprovalStore, ApprovalConflict
from autonomy_policy import Action, PolicyStore, evaluate, _KNOWN, _OPAQUE, _text

OWNER = "api-account"


def principal(auth):
    # Header-supplied identity/channel must never grant authority or forge audit identity.
    if auth.get("auth_type") == "shared_key":
        return OWNER, "shared-key-client"
    if auth.get("auth_type") == "session_token":
        return OWNER, "paired:" + _text(auth["identity"])
    raise PermissionError("Authenticated API account required")


def public_record(row):
    return {
        **{
            key: row[key]
            for key in (
                "id",
                "responsibility",
                "fingerprint",
                "created_at",
                "expires_at",
                "status",
                "decided_by",
                "decided_at",
                "decision",
            )
        },
        "preview": json.loads(row["preview_json"]),
        "scope": json.loads(row["scope_json"]),
    }


class ApprovalService:
    def __init__(self, directory):
        self.policy = PolicyStore(Path(directory) / "action-rules.json")
        self.approvals = ApprovalStore(Path(directory) / "approvals.sqlite")

    def recover_rules(self):
        self.approvals.publish_rules(self.policy)

    def decide(self, approval_id, auth, decision, fingerprint):
        owner, actor = principal(auth)
        with self.policy.locked():
            row = self.approvals.get(approval_id, owner=owner)
            scope = json.loads(row["scope_json"])
            if not scope:
                raise ValueError("Legacy request has no reviewable scope")
            data = self.policy.load()
            # Disabled mode and deny rules cannot be overridden by any approval.
            verdict, reason = evaluate(
                Action(**scope), data["rules"], enabled=data["enabled"]
            )
            if verdict == "deny" and decision in ("approve_once", "approve_always"):
                raise ApprovalConflict("Current policy blocks execution: " + reason)
            if decision == "approve_always":
                row, won = self.approvals.resolve_always(
                    approval_id, owner=owner, actor=actor, fingerprint=fingerprint
                )
                self.recover_rules()
                row = self.approvals.get(approval_id, owner=owner)
            else:
                row, won = self.approvals.resolve(
                    approval_id,
                    owner=owner,
                    actor=actor,
                    decision=decision,
                    fingerprint=fingerprint,
                )
            return {"request": public_record(row), "won": won}

    def execute(
        self,
        action,
        *,
        responsibility,
        intent_key,
        adapter,
        approval_id=None,
        summary,
        preflight=lambda: True,
        details=None,
    ):
        """Run a trusted synchronous adapter under current policy and single-use claim.

        Adapter must bind the canonical action to the actual operation; no model
        labels or generic CLI invocation may supply this callable. Keep external
        calls bounded. Lock prevents concurrent local policy revocation mid-start.
        """
        if action.operation not in _KNOWN or action.operation in _OPAQUE:
            raise PermissionError("No Always-On adapter for this capability")
        with self.policy.locked():
            data = self.policy.load()
            verdict, reason = evaluate(action, data["rules"], enabled=data["enabled"])
            if verdict == "deny":
                return {"status": "denied", "reason": reason}
            # Every execution reserves a durable intent, including policy-allowed work.
            row = self.approvals.create(
                action,
                owner=OWNER,
                responsibility=responsibility,
                intent_key=intent_key,
                preview=(
                    {"summary": summary, "details": details}
                    if details is not None
                    else {"summary": summary}
                ),
            )
            if approval_id and row["id"] != approval_id:
                raise ApprovalConflict("Approval does not belong to this intent")
            if verdict == "allow" and row["status"] == "pending":
                self.approvals.resolve(
                    row["id"],
                    owner=OWNER,
                    actor="policy",
                    decision="approve_once",
                    fingerprint=action.fingerprint,
                )
            if not preflight():
                return {"status": "paused", "approval_id": row["id"]}
            if not self.approvals.claim(
                row["id"], action, owner=OWNER, responsibility=responsibility
            ):
                return {
                    "status": self.approvals.get(row["id"], owner=OWNER)["status"],
                    "approval_id": row["id"],
                }
            try:
                result = adapter(action)
            except Exception:
                # A failure may have happened after an external side effect.
                self.approvals.finish(row["id"], owner=OWNER, outcome="uncertain")
                raise
            self.approvals.finish(row["id"], owner=OWNER, outcome="succeeded")
            return {"status": "succeeded", "approval_id": row["id"], "result": result}


def create_router(service, authenticate):
    from fastapi import APIRouter, Depends, HTTPException, Query, Request
    from pydantic import BaseModel, ConfigDict

    router = APIRouter(prefix="/api/v1/autonomy", tags=["Always-On"])

    class Decision(BaseModel):
        model_config = ConfigDict(extra="forbid")
        decision: str
        fingerprint: str

    class RuleInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        agent: str
        operation: str
        host: str
        resource: str
        decision: str
        path_prefix: bool = False

    def guarded(call):
        try:
            with service.policy.locked():
                return call()
        except KeyError:
            raise HTTPException(404, "Not found")
        except ApprovalConflict as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc))

    def scoped_record(approval_id, auth, agent):
        row = service.approvals.get(approval_id, owner=principal(auth)[0])
        if agent and json.loads(row["scope_json"]).get("agent") != agent:
            raise KeyError(approval_id)
        return row

    def scoped_rule(rule_id, agent):
        if agent and not any(r.id == rule_id and r.agent == agent for r in service.policy.load()["rules"]):
            raise KeyError(rule_id)

    @router.get("/approvals")
    def approvals(agent: str = "", auth=Depends(authenticate)):
        def read():
            owner, _ = principal(auth)
            service.recover_rules()
            return {
                "requests": [
                    public_record(row) for row in service.approvals.list(owner=owner)
                    if not agent or json.loads(row["scope_json"]).get("agent") == agent
                ]
            }

        return guarded(read)

    @router.get("/approvals/{approval_id}")
    def approval(approval_id: str, agent: str = "", auth=Depends(authenticate)):
        return guarded(
            lambda: public_record(
                scoped_record(approval_id, auth, agent)
            )
        )

    @router.post("/approvals/{approval_id}/decision")
    def decide(approval_id: str, body: Decision, agent: str = "", auth=Depends(authenticate)):
        return guarded(
            lambda: (scoped_record(approval_id, auth, agent), service.decide(approval_id, auth, body.decision, body.fingerprint))[1]
        )

    @router.get("/events")
    def events(after: int = Query(0, ge=0), auth=Depends(authenticate)):
        def read():
            owner, _ = principal(auth)
            service.approvals.list(owner=owner)  # expiry produces durable events
            rows = service.approvals.events(owner=owner, after=after)
            return {"events": rows, "cursor": rows[-1]["sequence"] if rows else after}

        return guarded(read)

    @router.get("/events/stream")
    async def stream(
        request: Request, after: int = Query(0, ge=0), auth=Depends(authenticate)
    ):
        import asyncio
        from fastapi.responses import StreamingResponse

        owner, _ = principal(auth)

        async def frames():
            cursor = after
            # Bounded connections reauthenticate on reconnect, including token expiry.
            for _ in range(30):
                if await request.is_disconnected():
                    return
                await asyncio.to_thread(service.approvals.list, owner=owner)
                rows = await asyncio.to_thread(
                    service.approvals.events, owner=owner, after=cursor
                )
                for event in rows:
                    cursor = event["sequence"]
                    yield "id: " + str(cursor) + "\ndata: " + json.dumps(event) + "\n\n"
                yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/rules")
    def rules(agent: str = "", auth=Depends(authenticate)):
        def read():
            principal(auth)
            data = service.policy.load()
            return {**data, "rules": [asdict(rule) for rule in data["rules"] if not agent or rule.agent == agent]}

        return guarded(read)

    @router.post("/rules")
    def add_rule(body: RuleInput, agent: str = "", auth=Depends(authenticate)):
        def add():
            _, actor = principal(auth)
            if agent and body.agent != agent:
                raise ValueError("Rule belongs to a different agent")
            if body.decision == "allow" and (
                body.operation not in _KNOWN or body.operation in _OPAQUE
            ):
                raise ValueError(
                    "Opaque or unknown actions cannot receive permanent grants"
                )
            return asdict(
                service.policy.add(
                    actor=actor, approval_id="manual-rule", **body.model_dump()
                )
            )

        return guarded(add)

    @router.put("/rules/{rule_id}")
    def edit_rule(rule_id: str, body: RuleInput, agent: str = "", auth=Depends(authenticate)):
        def edit():
            scoped_rule(rule_id, agent)
            _, actor = principal(auth)
            if agent and body.agent != agent:
                raise ValueError("Rule belongs to a different agent")
            if body.decision == "allow" and (
                body.operation not in _KNOWN or body.operation in _OPAQUE
            ):
                raise ValueError(
                    "Opaque or unknown actions cannot receive permanent grants"
                )
            return asdict(
                service.policy.replace(rule_id, actor=actor, **body.model_dump())
            )

        return guarded(edit)

    @router.delete("/rules/{rule_id}")
    def revoke_rule(rule_id: str, agent: str = "", auth=Depends(authenticate)):
        return guarded(
            lambda: (scoped_rule(rule_id, agent), asdict(service.policy.revoke(rule_id, actor=principal(auth)[1])))[1]
        )

    return router
