"""Durable, account-authenticated inbox federation; no execution authority on hub."""

import json
import os
import threading
from uuid import uuid4
from autonomy_service import OWNER, principal, public_record


class RequestRelay:
    def __init__(self, heartbeats):
        self.hb = heartbeats
        self.db = heartbeats.store.db
        self.lock = threading.Lock()
        with self.db._transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS inbox_identity (id TEXT PRIMARY KEY)"
            )
            db.execute(
                "INSERT INTO inbox_identity SELECT ? WHERE NOT EXISTS (SELECT 1 FROM inbox_identity)",
                (str(uuid4()),),
            )
            self.identity = db.execute("SELECT id FROM inbox_identity").fetchone()[0]
            db.execute(
                "CREATE TABLE IF NOT EXISTS inbox_peers (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at REAL NOT NULL)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS inbox_decisions (id TEXT PRIMARY KEY, origin TEXT NOT NULL, request_id TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, result TEXT, status TEXT NOT NULL DEFAULT 'queued', UNIQUE(origin,request_id,kind))"""
            )

    def local(self):
        return {
            "instance_id": self.identity,
            "approvals": [
                public_record(r)
                for r in self.hb.service.approvals.list(owner=OWNER)
                if r["status"] in ("pending", "rule_pending")
            ],
            "steering": self.hb.steering(),
        }

    def inbox(self):
        result = self.local()
        with self.db._transaction() as db:
            result["peers"] = []
            for row in db.execute("SELECT * FROM inbox_peers ORDER BY id LIMIT 32"):
                peer = json.loads(row["snapshot"])
                peer["last_seen_at"] = row["updated_at"]
                queued = {
                    (r["kind"], r["request_id"])
                    for r in db.execute(
                        "SELECT kind,request_id FROM inbox_decisions WHERE origin=? AND status='queued'",
                        (row["id"],),
                    )
                }
                for kind, field in [
                    ("approval", "approvals"),
                    ("steering", "steering"),
                ]:
                    for request in peer[field]:
                        if (kind, request["id"]) in queued:
                            request["status"] = "awaiting_origin"
                result["peers"].append(peer)
        return result

    def publish(self, origin, snapshot):
        # Only direct, finite snapshots; never accept transitive peers or credentials.
        if (
            origin == self.identity
            or snapshot.get("instance_id") != origin
            or set(snapshot) != {"instance_id", "approvals", "steering"}
        ):
            raise ValueError("Invalid origin snapshot")
        if len(json.dumps(snapshot).encode()) > 131072:
            raise ValueError("Snapshot too large")
        for field, version in [("approvals", "fingerprint"), ("steering", "revision")]:
            rows = snapshot[field]
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError("Invalid inbox list")
            for r in rows:
                if (
                    not isinstance(r, dict)
                    or not isinstance(r.get("id"), str)
                    or not isinstance(r.get(version), str)
                    or r.get("status") not in ("pending", "rule_pending")
                ):
                    raise ValueError("Invalid pending request")
        with self.db._transaction() as db:
            if (
                db.execute("SELECT count(*) FROM inbox_peers").fetchone()[0] >= 32
                and not db.execute(
                    "SELECT 1 FROM inbox_peers WHERE id=?", (origin,)
                ).fetchone()
            ):
                raise ValueError("Peer capacity reached")
            db.execute(
                "INSERT INTO inbox_peers VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot,updated_at=excluded.updated_at",
                (origin, json.dumps(snapshot), self.hb.store.clock()),
            )
        return {"received": True}

    def decide(self, origin, key, body, auth):
        principal(auth)
        kind = body.get("kind")
        if kind not in ("approval", "steering"):
            raise ValueError("Invalid request kind")
        if origin == self.identity:
            if kind == "approval":
                return self.hb.service.decide(
                    key, auth, body["decision"], body["fingerprint"]
                )
            return self.hb.answer(key, body["answer"], body["revision"])
        with self.db._transaction() as db:
            peer = db.execute(
                "SELECT snapshot FROM inbox_peers WHERE id=?", (origin,)
            ).fetchone()
            if peer is None:
                raise KeyError(origin)
            requests = json.loads(peer[0])[
                "approvals" if kind == "approval" else "steering"
            ]
            row = next((r for r in requests if r["id"] == key), None)
            if row is None:
                raise KeyError(key)
            version = "fingerprint" if kind == "approval" else "revision"
            if row[version] != body.get(version):
                raise ValueError("Request changed")
            if kind == "approval" and body.get("decision") not in (
                "approve_once",
                "reject",
            ):
                raise ValueError(
                    "Remote requests allow once or deny; permanent rules require origin review"
                )
            if kind == "steering" and (
                not isinstance(body.get("answer"), str)
                or not 1 <= len(body["answer"]) <= 4000
            ):
                raise ValueError("Invalid answer")
            prior = db.execute(
                "SELECT * FROM inbox_decisions WHERE origin=? AND request_id=? AND kind=?",
                (origin, key, kind),
            ).fetchone()
            if prior:
                return {"won": False, "status": prior["status"]}
            db.execute(
                "INSERT INTO inbox_decisions(id,origin,request_id,kind,payload) VALUES(?,?,?,?,?)",
                (str(uuid4()), origin, key, kind, json.dumps(body)),
            )
        return {"won": True, "status": "queued"}

    def pending(self, origin):
        with self.db._transaction() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM inbox_decisions WHERE origin=? AND status='queued' LIMIT 100",
                    (origin,),
                )
            ]

    def ack(self, origin, key, result):
        with self.db._transaction() as db:
            db.execute(
                "UPDATE inbox_decisions SET status='delivered',result=? WHERE id=? AND origin=? AND status='queued'",
                (json.dumps(result)[:8192], key, origin),
            )
        return {"received": True}

    def tick(self):
        url = os.environ.get("WEE_ALWAYS_ON_HUB_URL", "").rstrip("/")
        token = os.environ.get("WEE_ALWAYS_ON_HUB_TOKEN", "")
        if not url or not token or not self.lock.acquire(blocking=False):
            return
        try:
            import httpx

            with httpx.Client(
                timeout=8, headers={"Authorization": "Bearer " + token}
            ) as client:
                path = url + "/api/v1/autonomy/relay/" + self.identity
                client.put(path, json=self.local()).raise_for_status()
                response = client.get(path + "/decisions")
                response.raise_for_status()
                for row in response.json()["decisions"]:
                    try:
                        result = self.decide(
                            self.identity,
                            row["request_id"],
                            json.loads(row["payload"]),
                            {"auth_type": "shared_key"},
                        )
                    except (ValueError, KeyError, PermissionError):
                        result = {"status": "stale_or_rejected"}
                    ack = client.post(
                        path + "/decisions/" + row["id"] + "/ack", json=result
                    )
                    ack.raise_for_status()
        finally:
            self.lock.release()


def create_relay_router(relay, authenticate):
    from fastapi import APIRouter, Depends, HTTPException, Body

    router = APIRouter(prefix="/api/v1/autonomy")

    def guard(auth, call):
        try:
            principal(auth)
            return call()
        except KeyError:
            raise HTTPException(404, "Request not found")
        except (ValueError, PermissionError) as e:
            raise HTTPException(409, str(e))

    @router.put("/relay/{origin}")
    def publish(origin: str, body: dict = Body(...), auth=Depends(authenticate)):
        return guard(auth, lambda: relay.publish(origin, body))

    @router.get("/relay/{origin}/decisions")
    def pending(origin: str, auth=Depends(authenticate)):
        return guard(auth, lambda: {"decisions": relay.pending(origin)})

    @router.post("/relay/{origin}/decisions/{key}/ack")
    def ack(origin: str, key: str, body: dict = Body(...), auth=Depends(authenticate)):
        return guard(auth, lambda: relay.ack(origin, key, body))

    @router.post("/inbox/{origin}/{key}/decision")
    def decide(
        origin: str, key: str, body: dict = Body(...), auth=Depends(authenticate)
    ):
        return guard(auth, lambda: relay.decide(origin, key, body, auth))

    return router
