"""Adaptive agent cadence, user-authored goal remit and bounded progress adapters."""

import hashlib
import json
import re
from uuid import uuid4

from autonomy_policy import Action
from autonomy_service import OWNER, principal, public_record

MIN_DELAY, MAX_DELAY = 300, 14400


def delay(value):
    return max(MIN_DELAY, min(MAX_DELAY, value)) if type(value) is int else 3600


def revision(row):
    return hashlib.sha256(
        json.dumps(
            [
                row["goal"],
                row["autonomous_instructions"],
                row["permission_required_instructions"],
                row["instruction_version"],
            ]
        ).encode()
    ).hexdigest()


class Heartbeats:
    def __init__(self, store, service, agents):
        self.store, self.service, self.agents = store, service, agents
        with store.db._transaction() as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(responsibilities)")}
            for field, definition in [
                ("autonomous_instructions", "TEXT NOT NULL DEFAULT ''"),
                ("permission_required_instructions", "TEXT NOT NULL DEFAULT ''"),
                ("instruction_version", "INTEGER NOT NULL DEFAULT 0"),
            ]:
                if field not in columns:
                    db.execute(
                        f"ALTER TABLE responsibilities ADD COLUMN {field} {definition}"
                    )
            db.execute(
                """CREATE TABLE IF NOT EXISTS agent_heartbeats (
                agent TEXT PRIMARY KEY, next_at REAL NOT NULL, last_completed_at REAL NOT NULL DEFAULT 0,
                delay_seconds INTEGER NOT NULL DEFAULT 3600, reason TEXT NOT NULL DEFAULT 'Initial review', failures INTEGER NOT NULL DEFAULT 0)"""
            )
            if "generation" not in {
                r[1] for r in db.execute("PRAGMA table_info(agent_heartbeats)")
            }:
                db.execute(
                    "ALTER TABLE agent_heartbeats ADD COLUMN generation TEXT NOT NULL DEFAULT ''"
                )
            db.execute(
                """CREATE TABLE IF NOT EXISTS goal_steps (
                id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, run INTEGER NOT NULL, agent TEXT NOT NULL,
                revision TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                permission TEXT NOT NULL, quote TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                approval_id TEXT, result TEXT NOT NULL DEFAULT '', UNIQUE(goal_id,run,id))"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS steering_requests (
                id TEXT PRIMARY KEY, intent TEXT NOT NULL UNIQUE, goal_id TEXT NOT NULL, agent TEXT NOT NULL,
                revision TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL, resolved_at REAL)"""
            )

    def schedule(
        self, agent, seconds, reason, *, failed=False, generation=None, finish=False
    ):
        seconds = delay(seconds)
        reason = (
            reason
            if isinstance(reason, str) and 0 < len(reason) <= 512
            else "Scheduling output unavailable; bounded fallback"
        )
        now = self.store.clock()
        with self.store.db._transaction() as db:
            current = db.execute(
                "SELECT * FROM agent_heartbeats WHERE agent=?", (agent,)
            ).fetchone()
            if finish and current is not None and current["generation"] != generation:
                return dict(current)
            db.execute(
                """INSERT INTO agent_heartbeats(agent,next_at,last_completed_at,delay_seconds,reason,failures)
                VALUES(?,?,?,?,?,?) ON CONFLICT(agent) DO UPDATE SET next_at=excluded.next_at,
                last_completed_at=excluded.last_completed_at,delay_seconds=excluded.delay_seconds,
                reason=excluded.reason,failures=CASE WHEN ? THEN agent_heartbeats.failures+1 ELSE 0 END""",
                (agent, now + seconds, now, seconds, reason, int(failed), failed),
            )
            if generation is not None:
                db.execute(
                    "UPDATE agent_heartbeats SET generation=? WHERE agent=?",
                    (generation, agent),
                )
        return self.get(agent)

    def get(self, agent):
        with self.store.db._transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO agent_heartbeats(agent,next_at) VALUES(?,?)",
                (agent, self.store.clock()),
            )
            return dict(
                db.execute(
                    "SELECT * FROM agent_heartbeats WHERE agent=?", (agent,)
                ).fetchone()
            )

    def wake(self, agent):
        state = self.get(agent)
        earliest = (
            max(self.store.clock(), state["last_completed_at"] + MIN_DELAY)
            if state["last_completed_at"]
            else self.store.clock()
        )
        with self.store.db._transaction() as db:
            db.execute(
                "UPDATE agent_heartbeats SET next_at=MIN(next_at,?),reason=? WHERE agent=?",
                (earliest, "Goal or human input changed", agent),
            )

    def instructions(self, key, autonomous, ask):
        for value in (autonomous, ask):
            if (
                not isinstance(value, str)
                or len(value) > 8000
                or "\0" in value
                or "<!-- wee-autonomy-remit" in value
                or "<!-- /wee-autonomy-remit" in value
            ):
                raise ValueError(
                    "Instructions must be plain text, at most 8000 characters each"
                )
        with self.service.policy.locked(), self.store.db._transaction() as db:
            row = self.store._get(db, key, OWNER)
            if row["status"] == "cancelled":
                raise ValueError("Cancelled goal cannot be edited")
            db.execute(
                "UPDATE responsibilities SET autonomous_instructions=?,permission_required_instructions=?,instruction_version=instruction_version+1,status='paused',phase='idle',checkpoint='{}',run_number=run_number+1 WHERE id=?",
                (autonomous, ask, key),
            )
            db.execute(
                "UPDATE goal_steps SET status='invalidated' WHERE goal_id=? AND status='pending'",
                (key,),
            )
            db.execute(
                "UPDATE steering_requests SET status='invalidated' WHERE goal_id=? AND status='pending'",
                (key,),
            )
        for approval in self.service.approvals.list(owner=OWNER):
            if approval["responsibility"] == key and approval["status"] in (
                "pending",
                "approved",
            ):
                # A metadata mirror is a fresh explicit user action, not stale LLM work.
                self.service.approvals.cancel(
                    approval["id"], owner=OWNER, actor="goal-instructions-edited"
                )
        sources = getattr(self.store, "repository_goals", None)
        if sources and sources.source(key):
            source = sources.source(key)
            sources.submit(
                agent=row["agent"],
                repo=source["repo"],
                kind="instructions",
                payload={
                    "responsibility": key,
                    "autonomous_instructions": autonomous,
                    "permission_required_instructions": ask,
                },
                request_id=str(uuid4()),
            )
        self.wake(row["agent"])
        return self.store.get(key)

    def context(self, agent):
        goals = []
        sources = getattr(self.store, "repository_goals", None)
        for row in self.store.list():
            if row["agent"] != agent or row["status"] != "active":
                continue
            source = sources.source(row["id"]) if sources else None
            goals.append(
                {
                    "id": row["id"],
                    "goal": row["goal"],
                    "report": row["report"][:512],
                    "allowed_autonomously": row["autonomous_instructions"],
                    "ask_permission_first": row["permission_required_instructions"],
                    "issue": (
                        {"url": source["url"], "checklist": source["body"][:1000]}
                        if source
                        else None
                    ),
                    "blocked_on_input": self.blocked(row["id"]),
                }
            )
        with self.store.db._transaction() as db:
            answers = [
                dict(r)
                for r in db.execute(
                    "SELECT goal_id,question,answer FROM steering_requests WHERE agent=? AND status='answered' ORDER BY resolved_at DESC LIMIT 5",
                    (agent,),
                )
            ]
        return {
            "active_goals": goals,
            "steering_answers": answers,
            "heartbeat": self.get(agent),
        }

    def blocked(self, key):
        with self.store.db._transaction() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM goal_steps WHERE goal_id=? AND status='pending' UNION SELECT 1 FROM steering_requests WHERE goal_id=? AND status='pending' LIMIT 1",
                    (key, key),
                ).fetchone()
            )

    def propose(self, row, plan):
        for index, item in enumerate(plan.get("actions", [])):
            target = self.store.get(item.get("goal_id", row["id"]))
            if target["agent"] != row["agent"] or target["status"] != "active":
                raise ValueError("Action goal is not active for this agent")
            key = row["id"] + ":" + str(row["run_number"]) + ":" + str(index)
            with self.store.db._transaction() as db:
                db.execute(
                    "INSERT OR IGNORE INTO goal_steps(id,goal_id,run,agent,revision,kind,payload,permission,quote) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        key,
                        target["id"],
                        target["run_number"],
                        row["agent"],
                        revision(target),
                        item["kind"],
                        json.dumps(item["payload"]),
                        item.get("permission", "ask"),
                        item.get("instruction_quote", ""),
                    ),
                )
        for index, question in enumerate(plan.get("steering_questions", [])):
            self.ask(
                row["id"],
                question,
                row["id"] + ":" + str(row["run_number"]) + ":question:" + str(index),
            )

    def ask(self, key, question, intent=None):
        if not isinstance(question, str) or not 1 <= len(question) <= 2000:
            raise ValueError("Question must contain 1..2000 characters")
        row = self.store.get(key)
        with self.store.db._transaction() as db:
            if (
                db.execute(
                    "SELECT count(*) FROM steering_requests WHERE status='pending'"
                ).fetchone()[0]
                >= 100
            ):
                raise ValueError("Steering inbox capacity reached")
            db.execute(
                "INSERT OR IGNORE INTO steering_requests(id,intent,goal_id,agent,revision,question,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    intent or str(uuid4()),
                    key,
                    row["agent"],
                    revision(row),
                    question,
                    self.store.clock(),
                ),
            )
            return (
                dict(
                    db.execute(
                        "SELECT * FROM steering_requests WHERE intent=?", (intent,)
                    ).fetchone()
                )
                if intent
                else {"created": True}
            )

    def steering(self):
        with self.store.db._transaction() as db:
            rows = db.execute(
                "SELECT * FROM steering_requests WHERE status='pending' ORDER BY created_at LIMIT 100"
            ).fetchall()
            for r in rows:
                goal = self.store._get(db, r["goal_id"], OWNER)
                if goal["status"] == "cancelled" or revision(goal) != r["revision"]:
                    db.execute(
                        "UPDATE steering_requests SET status='invalidated' WHERE id=?",
                        (r["id"],),
                    )
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM steering_requests WHERE status='pending' ORDER BY created_at LIMIT 100"
                )
            ]

    def answer(self, key, answer, expected_revision):
        if not isinstance(answer, str) or not 1 <= len(answer) <= 4000:
            raise ValueError("Answer must contain 1..4000 characters")
        with self.service.policy.locked(), self.store.db._transaction() as db:
            row = db.execute(
                "SELECT * FROM steering_requests WHERE id=?", (key,)
            ).fetchone()
            if row is None:
                raise KeyError(key)
            goal = self.store._get(db, row["goal_id"], OWNER)
            if (
                row["revision"] != expected_revision
                or revision(goal) != row["revision"]
                or goal["status"] == "cancelled"
            ):
                raise ValueError("Goal changed; this question is stale")
            won = row["status"] == "pending"
            if won:
                db.execute(
                    "UPDATE steering_requests SET answer=?,status='answered',resolved_at=? WHERE id=?",
                    (answer, self.store.clock(), key),
                )
            result = dict(
                db.execute(
                    "SELECT * FROM steering_requests WHERE id=?", (key,)
                ).fetchone()
            )
        self.wake(row["agent"])
        return {"request": result, "won": won}

    def permission(self, row, step):
        # LLM interpretation must cite an exact user-authored allowance, and
        # cannot turn vague prose into arbitrary operations or defeat ask-first.
        terms = {
            "issue_comment": ("comment",),
            "issue_checklist": ("checklist", "issue"),
            "save_note": ("note", "report", "file"),
        }
        kind = step["kind"]
        required = terms.get(kind, ())
        ask = row["permission_required_instructions"].lower()
        if any(word in ask for word in required) or any(
            word in ask for word in ("every action", "all actions", "anything")
        ):
            return "ask"
        quote = step["quote"].strip()
        if any(
            word in quote.lower()
            for word in ("not ", "never ", "ask ", "permission", "approval")
        ):
            return "ask"
        allowed = row["autonomous_instructions"]
        if (
            step["permission"] == "autonomous"
            and quote
            and quote in allowed
            and any(word in quote.lower() for word in required)
        ):
            return "allow"
        return "ask"

    def process(self, worker):
        with self.store.db._transaction() as db:
            steps = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM goal_steps WHERE status='pending' ORDER BY rowid LIMIT 100"
                )
            ]
        for step in steps[:20]:
            row = self.store.get(step["goal_id"])
            sources = getattr(self.store, "repository_goals", None)
            valid = (
                row["status"] == "active"
                and row["phase"] != "attention"
                and row["run_number"] == step["run"]
                and revision(row) == step["revision"]
            )
            if valid and sources and sources.source(row["id"]):
                valid = (
                    sources.validate(row["id"])
                    and self.store.get(row["id"])["status"] == "active"
                )
            if not valid:
                with self.store.db._transaction() as db:
                    db.execute(
                        "UPDATE goal_steps SET status='invalidated' WHERE id=?",
                        (step["id"],),
                    )
                continue
            payload = json.loads(step["payload"])
            kind = step["kind"]
            if kind == "save_note":
                action = Action(
                    row["agent"],
                    "file.write",
                    "api-host",
                    str(worker.workspace(row["id"]) / "progress.md"),
                    json.dumps(
                        {"content": payload["content"], "responsibility": row["id"]}
                    ),
                )
                adapter = worker._write_report
            elif (
                kind in ("issue_checklist", "issue_comment")
                and sources
                and sources.source(row["id"])
            ):
                source = sources.source(row["id"])
                path = f'/repos/{source["repo"]}/issues/{source["number"]}'
                action = Action(
                    row["agent"],
                    "repository.modify",
                    "github.com",
                    path,
                    json.dumps(
                        {
                            "kind": kind,
                            "payload": payload,
                            "source_revision": source["revision"],
                        }
                    ),
                )

                def adapter(approved, path=path, source=source):
                    args = json.loads(approved.arguments_json)
                    if (
                        not sources.validate(row["id"])
                        or sources.source(row["id"])["revision"]
                        != args["source_revision"]
                    ):
                        raise ValueError("Issue changed")
                    if args["kind"] == "issue_comment":
                        return sources.github.request(
                            "POST",
                            path + "/comments",
                            {"body": args["payload"]["content"]},
                        )["html_url"]
                    # Checklist edits change only requested checkbox states; arbitrary
                    # model-authored issue replacement is not an autonomous adapter.
                    body = source["body"]
                    wanted = args["payload"]["items"]
                    lines = body.splitlines()
                    for change in wanted:
                        matched = False
                        for index, line in enumerate(lines):
                            match = re.match(r"^(\s*[-*]\s+\[)[ xX](\]\s+)(.*)$", line)
                            if match and match.group(3) == change["text"]:
                                lines[index] = (
                                    match.group(1)
                                    + ("x" if change["completed"] else " ")
                                    + match.group(2)
                                    + match.group(3)
                                )
                                matched = True
                        if not matched:
                            raise ValueError("Checklist item changed")
                    issue = sources.github.request(
                        "PATCH", path, {"body": "\n".join(lines)}
                    )
                    sources.ingest(source["repo"], issue)
                    refreshed = sources.source(row["id"])
                    if (
                        refreshed["eligible"]
                        and refreshed["issue_id"] == source["issue_id"]
                        and issue["title"] == source["title"]
                    ):
                        # This adapter changed only approved checkbox states. External
                        # issue changes still pause; its own bounded progress can continue.
                        self.store.control(row["id"], "resume")
                    return issue["html_url"]

            else:
                with self.store.db._transaction() as db:
                    db.execute(
                        "UPDATE goal_steps SET status='failed',result='Unsupported capability' WHERE id=?",
                        (step["id"],),
                    )
                continue
            try:
                outcome = self.service.execute(
                    action,
                    responsibility=row["id"],
                    intent_key="goal-step:" + step["id"],
                    adapter=adapter,
                    summary="Progress " + row["goal"] + ": " + kind,
                    details=action.arguments_json,
                    goal_decision=self.permission(row, step),
                    preflight=lambda: self.store.get(row["id"])["status"] == "active"
                    and revision(self.store.get(row["id"])) == step["revision"]
                    and self.store.get(row["id"])["run_number"] == step["run"],
                )
                status = outcome["status"]
                status = (
                    "pending"
                    if status in ("pending", "approved", "paused", "rule_pending")
                    else status
                )
            except Exception:
                outcome = {}
                status = "uncertain"
            if status == "uncertain":
                self.store.update(
                    row["id"],
                    phase="attention",
                    error="Progress action outcome uncertain; inspect and reconcile before resuming",
                )
            with self.store.db._transaction() as db:
                db.execute(
                    "UPDATE goal_steps SET status=?,approval_id=COALESCE(?,approval_id),result=? WHERE id=?",
                    (
                        status,
                        outcome.get("approval_id"),
                        str(outcome.get("result", ""))[:1024],
                        step["id"],
                    ),
                )


def create_heartbeat_router(heartbeats, authenticate):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, ConfigDict, Field

    router = APIRouter(prefix="/api/v1/autonomy", tags=["Adaptive Always-On"])

    class Instructions(BaseModel):
        model_config = ConfigDict(extra="forbid")
        autonomous_instructions: str = Field(max_length=8000)
        permission_required_instructions: str = Field(max_length=8000)

    class Answer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        answer: str = Field(min_length=1, max_length=4000)
        revision: str

    def guard(auth, call):
        try:
            principal(auth)
            return call()
        except KeyError:
            raise HTTPException(404, "Not found")
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc))

    @router.get("/heartbeats")
    def listing(agent: str = "", auth=Depends(authenticate)):
        return guard(
            auth,
            lambda: {
                "heartbeats": [
                    heartbeats.get(a)
                    for a in heartbeats.agents()
                    if not agent or a == agent
                ]
            },
        )

    @router.put("/responsibilities/{key}/instructions")
    def instructions(
        key: str, body: Instructions, agent: str = "", auth=Depends(authenticate)
    ):
        def save():
            if agent and heartbeats.store.get(key)["agent"] != agent:
                raise KeyError(key)
            from autonomy_coordinator import public_responsibility

            return public_responsibility(
                heartbeats.instructions(
                    key,
                    body.autonomous_instructions,
                    body.permission_required_instructions,
                )
            )

        return guard(auth, save)

    @router.post("/steering/{key}/answer")
    def answer(key: str, body: Answer, auth=Depends(authenticate)):
        return guard(auth, lambda: heartbeats.answer(key, body.answer, body.revision))

    @router.get("/inbox")
    def inbox(auth=Depends(authenticate)):
        return guard(auth, lambda: heartbeats.store.request_relay.inbox())

    return router
