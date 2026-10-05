"""Durable, opt-in responsibilities with an exclusive constrained worker.

This worker never invokes an agent CLI, SDK tool, browser, shell or delegate.
Initial capability: maintain reports in a private per-responsibility workspace.
Providers propose data; trusted adapters construct and gate actual operations.
"""

import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import time
from uuid import uuid4
from autonomy_approvals import ApprovalStore
from autonomy_policy import Action, _text
from autonomy_service import OWNER, principal


class ResponsibilityStore:
    def __init__(self, directory, *, clock=time.time):
        self.directory = Path(directory).resolve()
        self.clock = clock
        self.db = ApprovalStore(self.directory / "responsibilities.sqlite", clock=clock)
        with self.db._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS responsibilities (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, agent TEXT NOT NULL,
                goal TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
                status TEXT NOT NULL, phase TEXT NOT NULL, next_at REAL NOT NULL,
                run_number INTEGER NOT NULL DEFAULT 0, checkpoint TEXT NOT NULL DEFAULT '{}',
                report TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '')""")

    def create(self, *, agent, goal, interval_seconds, owner=OWNER):
        _text(agent)
        _text(goal)
        _text(owner)
        if type(interval_seconds) is not int or not 300 <= interval_seconds <= 604800:
            raise ValueError("Schedule interval must be 5 minutes..7 days")
        with self.db._transaction() as db:
            if (
                db.execute(
                    "SELECT count(*) FROM responsibilities WHERE owner=? AND status!='cancelled'",
                    (owner,),
                ).fetchone()[0]
                >= 20
            ):
                raise ValueError("Responsibility limit reached (20)")
            if (
                db.execute("SELECT count(*) FROM responsibilities").fetchone()[0]
                >= 1000
            ):
                raise ValueError("Responsibility audit capacity reached")
            key = str(uuid4())
            db.execute(
                "INSERT INTO responsibilities(id,owner,agent,goal,interval_seconds,status,phase,next_at) VALUES(?,?,?,?,?,'paused','idle',?)",
                (key, owner, agent, goal, interval_seconds, self.clock()),
            )
            return self._get(db, key, owner)

    @staticmethod
    def _get(db, key, owner):
        row = db.execute(
            "SELECT * FROM responsibilities WHERE id=? AND owner=?", (key, owner)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return dict(row)

    def get(self, key, owner=OWNER):
        with self.db._transaction() as db:
            return self._get(db, key, owner)

    def list(self, owner=OWNER):
        with self.db._transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM responsibilities WHERE owner=? ORDER BY next_at",
                    (owner,),
                )
            ]

    def control(self, key, command, owner=OWNER):
        if command not in ("resume", "pause", "cancel", "reconcile"):
            raise ValueError("Unknown responsibility control")
        with self.db._transaction() as db:
            row = self._get(db, key, owner)
            if row["status"] == "cancelled":
                raise ValueError("Cancelled responsibilities cannot resume")
            status = {
                "resume": "active",
                "pause": "paused",
                "cancel": "cancelled",
                "reconcile": "paused",
            }[command]
            if command == "reconcile":
                # Human explicitly acknowledges uncertain work before a new run.
                if row["phase"] != "attention":
                    raise ValueError("No uncertain work to reconcile")
                db.execute(
                    "UPDATE responsibilities SET phase='idle', checkpoint='{}', error='', next_at=? WHERE id=?",
                    (self.clock() + row["interval_seconds"], key),
                )
            db.execute("UPDATE responsibilities SET status=? WHERE id=?", (status, key))
            return self._get(db, key, owner)

    def revise(self, key, goal, owner=OWNER):
        _text(goal)
        with self.db._transaction() as db:
            row = self._get(db, key, owner)
            if row["status"] == "cancelled":
                raise ValueError("Cancelled responsibility cannot be revised")
            db.execute(
                "UPDATE responsibilities SET goal=?, status='paused', phase='idle', checkpoint='{}', error='', run_number=run_number+1, next_at=? WHERE id=?",
                (goal, self.clock(), key),
            )
            return self._get(db, key, owner)

    def update(self, key, **fields):
        if not fields or set(fields) - {
            "phase",
            "next_at",
            "run_number",
            "checkpoint",
            "report",
            "error",
        }:
            raise ValueError("Invalid checkpoint fields")
        with self.db._transaction() as db:
            self._get(db, key, OWNER)
            db.execute(
                "UPDATE responsibilities SET "
                + ", ".join(k + "=?" for k in fields)
                + " WHERE id=?",
                (*fields.values(), key),
            )

    def update_if_run(self, key, run, **fields):
        if not fields or set(fields) - {
            "phase",
            "next_at",
            "run_number",
            "checkpoint",
            "report",
            "error",
        }:
            raise ValueError("Invalid checkpoint fields")
        with self.db._transaction() as db:
            row = self._get(db, key, OWNER)
            if row["run_number"] != run or row["status"] == "cancelled":
                return False
            db.execute(
                "UPDATE responsibilities SET "
                + ", ".join(k + "=?" for k in fields)
                + " WHERE id=?",
                (*fields.values(), key),
            )
            return True

    def recover(self):
        with self.db._transaction() as db:
            db.execute(
                "UPDATE responsibilities SET phase='attention', error='Worker interrupted; inspect activity and reconcile before resuming' WHERE phase='running'"
            )


def public_responsibility(row):
    return {
        key: row[key]
        for key in (
            "id",
            "agent",
            "goal",
            "interval_seconds",
            "status",
            "phase",
            "next_at",
            "run_number",
            "report",
            "error",
        )
    }


class Coordinator:
    def __init__(self, service, store, agents, planner):
        self.service, self.store, self.agents, self.planner = (
            service,
            store,
            agents,
            planner,
        )
        self.lock_fd = None
        self.last_key = None

    def acquire(self):
        fd = os.open(
            self.store.directory / "worker.lock", os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self.lock_fd = fd
        self.store.recover()
        self.service.approvals.recover_claims()
        self.service.recover_rules()
        return True

    def close(self):
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.lock_fd = None

    def _workspace_fd(self, key):
        # Each component opens relative to a trusted descriptor without following links.
        root = os.open(
            self.store.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            try:
                os.mkdir("workspaces", mode=0o700, dir_fd=root)
            except FileExistsError:
                pass
            parent = os.open(
                "workspaces", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
            )
            try:
                try:
                    os.mkdir(key, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                return os.open(
                    key, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                )
            finally:
                os.close(parent)
        finally:
            os.close(root)

    def workspace(self, key):
        # Keys originate from UUID-backed durable responsibilities, never model input.
        self.store.get(key)
        fd = self._workspace_fd(key)
        os.close(fd)
        return self.store.directory / "workspaces" / key

    def _write_report(self, action):
        """Replace a bounded report using anchored directory descriptors, no symlink traversal."""
        args = json.loads(action.arguments_json)
        path = Path(action.resource)
        root = self.workspace(args["responsibility"])
        relative = PurePosixPath(path).relative_to(PurePosixPath(root))
        if len(relative.parts) != 1 or relative.name != "report.md":
            raise PermissionError("Unsupported report destination")
        fd = self._workspace_fd(args["responsibility"])
        temporary = "." + str(uuid4()) + ".tmp"
        try:
            output = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=fd,
            )
            with os.fdopen(output, "w") as handle:
                handle.write(args["content"])
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, relative.name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
            os.close(fd)
        return "Report saved"

    def step(self):
        if self.lock_fd is None:
            raise RuntimeError("Exclusive worker lease required")
        # One responsibility per tick; no unbounded dispatch/overlap.
        if not self.service.policy.load()["enabled"]:
            return
        rows = self.store.list()
        if self.last_key in [r["id"] for r in rows]:
            index = [r["id"] for r in rows].index(self.last_key) + 1
            rows = rows[index:] + rows[:index]
        for row in rows:
            if row["status"] != "active" or row["phase"] == "attention":
                continue
            if row["phase"] == "idle" and row["next_at"] > self.store.clock():
                continue
            key = row["id"]
            self.last_key = key
            if row["agent"] not in self.agents():
                self.store.update(
                    key, phase="attention", error="Agent no longer available"
                )
                return
            try:
                if row["phase"] in ("idle", "model_waiting"):
                    run = (
                        row["run_number"] + 1
                        if row["phase"] == "idle"
                        else row["run_number"]
                    )
                    if not self.store.update_if_run(
                        key,
                        row["run_number"],
                        phase="running",
                        run_number=run,
                        error="",
                    ):
                        return
                    row = self.store.get(key)
                    if row["run_number"] != run:
                        return
                    plan = self.planner(row)
                    if (
                        not isinstance(plan, dict)
                        or set(plan) != {"report"}
                        or not isinstance(plan["report"], str)
                        or not 1 <= len(plan["report"]) <= 4096
                    ):
                        raise ValueError("Planner must return a bounded report")
                    checkpoint = json.dumps(
                        {"report": plan["report"], "run_number": run}
                    )
                    if not self.store.update_if_run(
                        key, run, phase="waiting", checkpoint=checkpoint
                    ):
                        return
                    row = self.store.get(key)
                if row["status"] != "active":
                    return
                plan = json.loads(row["checkpoint"])
                action = Action(
                    row["agent"],
                    "file.write",
                    "api-host",
                    str(self.workspace(key) / "report.md"),
                    json.dumps({"responsibility": key, "content": plan["report"]}),
                )
                result = self.service.execute(
                    action,
                    responsibility=key,
                    intent_key=key + ":" + str(plan["run_number"]),
                    summary="Save report for "
                    + row["agent"]
                    + " in its isolated Always-On workspace",
                    adapter=self._write_report,
                    details=plan["report"],
                    preflight=lambda: self.store.get(key)["status"] == "active"
                    and self.store.get(key)["run_number"] == plan["run_number"],
                )
                status = result["status"]
                if status == "succeeded":
                    self.store.update_if_run(
                        key,
                        plan["run_number"],
                        phase="idle",
                        checkpoint="{}",
                        report=plan["report"],
                        next_at=self.store.clock() + row["interval_seconds"],
                        error="",
                    )
                elif status in (
                    "rejected",
                    "revision_requested",
                    "expired",
                    "cancelled",
                    "invalidated",
                    "uncertain",
                    "failed",
                    "denied",
                ):
                    self.store.update_if_run(
                        key,
                        plan["run_number"],
                        phase="attention",
                        error="Action "
                        + status
                        + "; inspect and revise/reconcile before a new run",
                    )
                return
            except Exception as exc:
                from autonomy_models import ModelWaiting, BudgetExceeded

                if isinstance(exc, ModelWaiting):
                    self.store.update_if_run(
                        key,
                        row["run_number"],
                        phase="model_waiting",
                        checkpoint=json.dumps(
                            {
                                "model_approval": exc.approval_id,
                                "run_number": row["run_number"],
                            }
                        ),
                    )
                    return
                if isinstance(exc, BudgetExceeded):
                    self.store.update_if_run(
                        key, row["run_number"], phase="attention", error=str(exc)
                    )
                    return
                # Never persist provider credentials, arbitrary exception bodies, or tool output.
                self.store.update_if_run(
                    key,
                    row["run_number"],
                    phase="attention",
                    error="Run interrupted or failed; inspect and reconcile before resuming",
                )
                return


def create_responsibility_router(store, service, authenticate, agents):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, ConfigDict

    router = APIRouter(prefix="/api/v1/autonomy", tags=["Always-On"])

    class Create(BaseModel):
        model_config = ConfigDict(extra="forbid")
        agent: str
        goal: str
        interval_seconds: int = 3600

    class Revise(BaseModel):
        model_config = ConfigDict(extra="forbid")
        goal: str

    class Control(BaseModel):
        model_config = ConfigDict(extra="forbid")
        command: str

    def guarded(call):
        try:
            return call()
        except KeyError:
            raise HTTPException(404, "Not found")
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc))

    @router.get("/responsibilities")
    def listing(agent: str = "", auth=Depends(authenticate)):
        return guarded(
            lambda: {
                "responsibilities": [
                    public_responsibility(r) for r in store.list(principal(auth)[0]) if not agent or r["agent"] == agent
                ]
            }
        )

    @router.post("/responsibilities")
    def create(body: Create, agent: str = "", auth=Depends(authenticate)):
        def add():
            owner, _ = principal(auth)
            if agent and body.agent != agent:
                raise ValueError("Responsibility belongs to a different agent")
            if body.agent not in agents():
                raise ValueError("Unknown agent")
            return public_responsibility(store.create(owner=owner, **body.model_dump()))

        return guarded(add)

    @router.put("/responsibilities/{key}")
    def revise(key: str, body: Revise, agent: str = "", auth=Depends(authenticate)):
        def change():
            owner, _ = principal(auth)
            if agent and store.get(key, owner)["agent"] != agent:
                raise KeyError(key)
            with service.policy.locked():
                return public_responsibility(store.revise(key, body.goal, owner))

        return guarded(change)

    @router.post("/responsibilities/{key}/control")
    def control(key: str, body: Control, agent: str = "", auth=Depends(authenticate)):
        def change():
            owner, actor = principal(auth)
            if agent and store.get(key, owner)["agent"] != agent:
                raise KeyError(key)
            with service.policy.locked():
                row = store.control(key, body.command, owner)
                if body.command == "resume":
                    service.policy.set_enabled(True)
                if body.command == "cancel":
                    with service.approvals._transaction() as db:
                        ids = [
                            r[0]
                            for r in db.execute(
                                "SELECT id FROM approvals WHERE owner=? AND responsibility=? AND status IN ('pending','approved')",
                                (owner, key),
                            )
                        ]
                    for approval_id in ids:
                        service.approvals.cancel(approval_id, owner=owner, actor=actor)
                return public_responsibility(row)

        return guarded(change)

    return router
