"""Opt-in APNs alerts for durable inbox requests (including relayed origins)."""

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from autonomy_service import principal


class InboxPush:
    def __init__(self, relay):
        self.relay = relay
        self.db = relay.db
        with self.db._transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS inbox_devices (id TEXT PRIMARY KEY, token TEXT NOT NULL, environment TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS inbox_push_delivery (id TEXT PRIMARY KEY, device TEXT NOT NULL, status TEXT NOT NULL, next_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)"
            )

    def register(self, device, token, environment):
        if (
            not isinstance(device, str)
            or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", device)
            or not isinstance(token, str)
            or not re.fullmatch(r"[0-9a-fA-F]{32,200}", token)
            or environment not in ("sandbox", "production")
        ):
            raise ValueError("Invalid APNs registration")
        with self.db._transaction() as db:
            if (
                db.execute("SELECT count(*) FROM inbox_devices").fetchone()[0] >= 100
                and not db.execute(
                    "SELECT 1 FROM inbox_devices WHERE id=?", (device,)
                ).fetchone()
            ):
                raise ValueError("Device capacity reached")
            db.execute(
                "INSERT INTO inbox_devices VALUES(?,?,?,1) ON CONFLICT(id) DO UPDATE SET token=excluded.token,environment=excluded.environment,enabled=1",
                (device, token, environment),
            )
        return {"registered": True}

    def remove(self, device):
        with self.db._transaction() as db:
            db.execute("DELETE FROM inbox_devices WHERE id=?", (device,))
        return {"removed": True}

    @staticmethod
    def jwt():
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils

        def encode(value):
            return base64.urlsafe_b64encode(value).rstrip(b"=")

        head = encode(
            json.dumps(
                {"alg": "ES256", "kid": os.environ["WEE_APNS_KEY_ID"]},
                separators=(",", ":"),
            ).encode()
        )
        body = encode(
            json.dumps(
                {"iss": os.environ["WEE_APNS_TEAM_ID"], "iat": int(time.time())},
                separators=(",", ":"),
            ).encode()
        )
        key = serialization.load_pem_private_key(
            Path(os.environ["WEE_APNS_KEY_FILE"]).read_bytes(), password=None
        )
        message = head + b"." + body
        r, s = utils.decode_dss_signature(key.sign(message, ec.ECDSA(hashes.SHA256())))
        return (
            message + b"." + encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        ).decode()

    def send(self, device, request, delivery):
        import httpx

        host = (
            "api.sandbox.push.apple.com"
            if device["environment"] == "sandbox"
            else "api.push.apple.com"
        )
        payload = {
            "aps": {
                "alert": {
                    "title": "Wee agent needs your input",
                    "body": "Open Wee to review an approval or steering request.",
                },
                "sound": "default",
            },
            **request,
        }
        with httpx.Client(http2=True, timeout=8) as client:
            response = client.post(
                "https://" + host + "/3/device/" + device["token"],
                json=payload,
                headers={
                    "authorization": "bearer " + self.jwt(),
                    "apns-topic": os.environ["WEE_APNS_TOPIC"],
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    "apns-collapse-id": delivery[:64],
                },
            )
            if response.status_code == 410:
                with self.db._transaction() as db:
                    db.execute(
                        "UPDATE inbox_devices SET enabled=0 WHERE id=?", (device["id"],)
                    )
            response.raise_for_status()

    def tick(self, sender=None):
        if sender is None and not all(
            os.environ.get(k)
            for k in (
                "WEE_APNS_KEY_FILE",
                "WEE_APNS_KEY_ID",
                "WEE_APNS_TEAM_ID",
                "WEE_APNS_TOPIC",
            )
        ):
            return
        sender = sender or self.send
        inbox = self.relay.inbox()
        requests = []
        for group in [inbox, *inbox.get("peers", [])]:
            for kind, field, version in [
                ("approval", "approvals", "fingerprint"),
                ("steering", "steering", "revision"),
            ]:
                for row in group[field]:
                    if row["status"] not in ("pending", "rule_pending"):
                        continue
                    requests.append(
                        {
                            "origin": group["instance_id"],
                            "request_id": row["id"],
                            "kind": kind,
                            "version": row[version],
                        }
                    )
        with self.db._transaction() as db:
            devices = [
                dict(r)
                for r in db.execute("SELECT * FROM inbox_devices WHERE enabled=1")
            ]
        sent = 0
        now = time.time()
        for request in requests:
            for device in devices:
                delivery = hashlib.sha256(
                    json.dumps([device["id"], request], sort_keys=True).encode()
                ).hexdigest()
                with self.db._transaction() as db:
                    db.execute(
                        "INSERT OR IGNORE INTO inbox_push_delivery VALUES(?,?,'pending',0,0)",
                        (delivery, device["id"]),
                    )
                    row = db.execute(
                        "SELECT * FROM inbox_push_delivery WHERE id=?", (delivery,)
                    ).fetchone()
                    if row["status"] == "sent" or row["next_at"] > now:
                        continue
                    # Reserve before sending; a crashed sender becomes retryable.
                    db.execute(
                        "UPDATE inbox_push_delivery SET next_at=?,attempts=attempts+1 WHERE id=?",
                        (now + 120, delivery),
                    )
                try:
                    sender(device, request, delivery)
                except Exception:
                    with self.db._transaction() as db:
                        db.execute(
                            "UPDATE inbox_push_delivery SET next_at=? WHERE id=?",
                            (
                                now + min(14400, 60 * 2 ** min(row["attempts"], 8)),
                                delivery,
                            ),
                        )
                else:
                    with self.db._transaction() as db:
                        db.execute(
                            "UPDATE inbox_push_delivery SET status='sent' WHERE id=?",
                            (delivery,),
                        )
                sent += 1
                if sent >= 5:
                    return


def create_push_router(push, authenticate):
    from fastapi import APIRouter, Body, Depends, HTTPException

    router = APIRouter(prefix="/api/v1/autonomy/inbox/devices")

    @router.put("/{device}")
    def register(device: str, body: dict = Body(...), auth=Depends(authenticate)):
        try:
            principal(auth)
            return push.register(device, body.get("token"), body.get("environment"))
        except (PermissionError, ValueError) as e:
            raise HTTPException(400, str(e))

    @router.delete("/{device}")
    def remove(device: str, auth=Depends(authenticate)):
        principal(auth)
        return push.remove(device)

    return router
