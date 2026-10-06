"""Repository-backed Always-On goals. GitHub owns intent; SQLite owns execution."""

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from uuid import uuid4

from autonomy_policy import Action
from autonomy_service import OWNER, principal

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_AGENT = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def repository(value):
    if (
        not isinstance(value, str)
        or not _REPO.fullmatch(value)
        or any(p in (".", "..") for p in value.split("/"))
    ):
        raise ValueError("Repository must be owner/name on github.com")
    return value.lower()


class GitHubUnavailable(Exception):
    """Sanitized failure; provider responses and credentials never enter audit."""


class GitHub:
    def request(self, method, path, body=None):
        token = (
            os.environ.get("WEE_GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
        )
        if not token:
            # Reuse the API service account's existing GitHub CLI login. The
            # credential stays in memory and never enters action previews/logs.
            try:
                login = subprocess.run(
                    ["gh", "auth", "token", "--hostname", "github.com"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if login.returncode == 0:
                    token = login.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        if not token:
            raise GitHubUnavailable(
                "Configure WEE_GITHUB_TOKEN or sign in to GitHub CLI for the API service account"
            )
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Wee-Always-On",
        }
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            "https://api.github.com" + path, data=data, headers=headers, method=method
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        try:
            with urllib.request.build_opener(NoRedirect()).open(
                req, timeout=10
            ) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise GitHubUnavailable("GitHub response exceeded the bound")
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise GitHubUnavailable(
                "GitHub request failed (HTTP " + str(exc.code) + ")"
            ) from None
        except (OSError, ValueError):
            raise GitHubUnavailable(
                "GitHub request failed; check authentication and connectivity"
            ) from None

    def issue(self, repo, number):
        return self.request("GET", f"/repos/{repository(repo)}/issues/{number}")

    def issues(self, repo):
        result = []
        # Complete snapshot or exception: a partial response never becomes deletions.
        for page in range(1, 11):
            rows = self.request(
                "GET",
                f"/repos/{repository(repo)}/issues?state=open&labels=always-on&per_page=100&page={page}",
            )
            if not isinstance(rows, list):
                raise GitHubUnavailable("Invalid GitHub issue list")
            result.extend(r for r in rows if "pull_request" not in r)
            if len(rows) < 100:
                return result
        raise GitHubUnavailable("Repository exceeds the bounded issue sync limit")

    def ensure_label(self, repo, name):
        from urllib.parse import quote

        try:
            self.request("GET", f"/repos/{repo}/labels/{quote(name, safe='')}")
        except GitHubUnavailable as exc:
            if "HTTP 404" not in str(exc):
                raise
            try:
                self.request(
                    "POST", f"/repos/{repo}/labels", {"name": name, "color": "5319e7"}
                )
            except GitHubUnavailable as race:
                if "HTTP 422" not in str(race):
                    raise


class RepositoryGoals:
    def __init__(self, store, service, agents, github=None):
        self.store, self.service, self.agents = store, service, agents
        self.github = github or GitHub()
        self.lock = threading.RLock()
        self.next_sync = 0
        self.last_operation = None
        with store.db._transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS goal_repositories (repo TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS goal_repo_attention (repo TEXT NOT NULL, number INTEGER NOT NULL, title TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(repo,number))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS goal_repo_sync (repo TEXT PRIMARY KEY, checked_at REAL NOT NULL, error TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS goal_repo_defaults (agent TEXT PRIMARY KEY, repo TEXT NOT NULL)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS goal_sources (
                responsibility TEXT PRIMARY KEY, repo TEXT NOT NULL, number INTEGER NOT NULL,
                issue_id INTEGER NOT NULL UNIQUE, title TEXT NOT NULL, body TEXT NOT NULL,
                revision TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'recurring',
                eligible INTEGER NOT NULL, sync_at REAL NOT NULL, sync_error TEXT NOT NULL DEFAULT '',
                UNIQUE(repo,number))""")
            db.execute(
                """CREATE TABLE IF NOT EXISTS goal_repo_operations (
                id TEXT PRIMARY KEY, agent TEXT NOT NULL, repo TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                approval_id TEXT, result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '')"""
            )

    def settings(self, agent=""):
        with self.store.db._transaction() as db:
            repos = [
                dict(r)
                for r in db.execute(
                    "SELECT repo,enabled FROM goal_repositories ORDER BY repo"
                )
            ]
            attention = [
                dict(r)
                for r in db.execute(
                    "SELECT a.* FROM goal_repo_attention a JOIN goal_repositories r ON a.repo=r.repo WHERE r.enabled=1 ORDER BY a.repo,a.number LIMIT 100"
                )
            ]
            sync = {
                r["repo"]: dict(r) for r in db.execute("SELECT * FROM goal_repo_sync")
            }
            defaults = dict(
                db.execute("SELECT agent,repo FROM goal_repo_defaults").fetchall()
            )
        return {
            "repositories": [
                {
                    "repository": r["repo"],
                    "enabled": bool(r["enabled"]),
                    "sync": sync.get(r["repo"], {"checked_at": 0, "error": ""}),
                }
                for r in repos
            ],
            "default_repository": defaults.get(agent, ""),
            "attention": attention,
            "defaults": defaults if not agent else {agent: defaults.get(agent, "")},
        }

    def configure(self, repositories, default, agent=""):
        if agent and agent not in self.agents():
            raise ValueError("Unknown agent")
        if not isinstance(repositories, list) or len(repositories) > 20:
            raise ValueError("At most 20 work repositories")
        normalized = {}
        for item in repositories:
            if (
                set(item) != {"repository", "enabled"}
                or type(item["enabled"]) is not bool
            ):
                raise ValueError("Invalid repository settings")
            repo = repository(item["repository"])
            if repo in normalized:
                raise ValueError("Duplicate repository")
            normalized[repo] = item["enabled"]
        default = repository(default) if default else ""
        if default and (not agent or not normalized.get(default)):
            raise ValueError("Default must be an enabled repository for an agent")
        with self.service.policy.locked(), self.lock, self.store.db._transaction() as db:
            db.execute("DELETE FROM goal_repositories")
            db.executemany(
                "INSERT INTO goal_repositories(repo,enabled) VALUES(?,?)",
                normalized.items(),
            )
            if agent:
                db.execute("DELETE FROM goal_repo_defaults WHERE agent=?", (agent,))
                if default:
                    db.execute(
                        "INSERT INTO goal_repo_defaults VALUES(?,?)", (agent, default)
                    )
            db.execute(
                "DELETE FROM goal_repo_defaults WHERE repo NOT IN (SELECT repo FROM goal_repositories WHERE enabled=1)"
            )
            for row in db.execute(
                "SELECT responsibility,repo FROM goal_sources"
            ).fetchall():
                if not normalized.get(row["repo"]):
                    self._invalidate(
                        db,
                        row["responsibility"],
                        "Work repository is disabled or removed",
                    )
        self.next_sync = 0
        return self.settings(agent)

    def allowed(self, repo):
        repo = repository(repo)
        if not any(
            r["repository"] == repo and r["enabled"]
            for r in self.settings()["repositories"]
        ):
            raise ValueError("Repository is not configured and enabled")
        return repo

    def source(self, key):
        with self.store.db._transaction() as db:
            row = db.execute(
                "SELECT * FROM goal_sources WHERE responsibility=?", (key,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["url"] = f'https://github.com/{result["repo"]}/issues/{result["number"]}'
        return result

    def enrich(self, row):
        row = dict(row)
        row["source"] = self.source(row["id"])
        row["tracking"] = "github" if row["source"] else "unlinked"
        return row

    @staticmethod
    def _invalidate(db, key, reason):
        db.execute(
            "UPDATE goal_sources SET eligible=0,sync_error=? WHERE responsibility=?",
            (reason, key),
        )
        db.execute(
            """UPDATE responsibilities SET status=CASE WHEN status='cancelled' THEN status ELSE 'paused' END,
                   phase='idle',checkpoint='{}',run_number=run_number+1,error=? WHERE id=?""",
            (reason, key),
        )

    def ingest(self, repo, issue, *, interval=3600, mode="recurring", migrate=None):
        repo = self.allowed(repo)
        if (
            mode not in ("recurring", "finite")
            or type(interval) is not int
            or not 300 <= interval <= 604800
        ):
            raise ValueError("Invalid mode or schedule interval")
        if "pull_request" in issue:
            raise ValueError("Pull requests cannot be Always-On goals")
        number, identity = issue.get("number"), issue.get("id")
        if type(number) is not int or number < 1 or type(identity) is not int:
            raise ValueError("Invalid GitHub issue identity")
        # A transferred issue returned through a redirect must never retain its former binding.
        if (
            issue.get("html_url", "").lower()
            != f"https://github.com/{repo}/issues/{number}"
        ):
            raise ValueError("GitHub issue belongs to a different repository")
        title, body = issue.get("title"), issue.get("body") or ""
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > 256
            or not isinstance(body, str)
            or len(body) > 60000
        ):
            raise ValueError("Invalid or oversized issue content")
        labels = [r["name"] for r in issue.get("labels", [])]
        assigned = [label[6:] for label in labels if label.startswith("agent:")]
        valid_agent = (
            len(assigned) == 1
            and _AGENT.fullmatch(assigned[0])
            and assigned[0] in self.agents()
        )
        queue_conflict = any(
            label in self.agents()
            or label in ("in-progress", "queued")
            or label.endswith(":in-progress")
            for label in labels
        )
        eligible = (
            issue.get("state") == "open"
            and "always-on" in labels
            and bool(valid_agent)
            and not queue_conflict
        )
        reason = (
            ""
            if eligible
            else (
                "Remove normal agent queue/in-progress labels before Always-On dispatch"
                if queue_conflict
                else "Issue closed, unflagged, or agent assignment missing/ambiguous"
            )
        )
        revision = hashlib.sha256(
            json.dumps([title, body, sorted(labels), issue.get("state")]).encode()
        ).hexdigest()
        now = self.store.clock()
        with self.service.policy.locked(), self.lock, self.store.db._transaction() as db:
            if not eligible and issue.get("state") == "open" and "always-on" in labels:
                db.execute(
                    "INSERT OR REPLACE INTO goal_repo_attention VALUES(?,?,?,?)",
                    (repo, number, title, reason),
                )
            else:
                db.execute(
                    "DELETE FROM goal_repo_attention WHERE repo=? AND number=?",
                    (repo, number),
                )
            old = db.execute(
                "SELECT * FROM goal_sources WHERE repo=? AND number=?", (repo, number)
            ).fetchone()
            other = db.execute(
                "SELECT * FROM goal_sources WHERE issue_id=?", (identity,)
            ).fetchone()
            if other and (other["repo"], other["number"]) != (repo, number):
                self._invalidate(
                    db,
                    other["responsibility"],
                    "Issue was transferred; explicit relink required",
                )
                return None
            if old and old["issue_id"] != identity:
                self._invalidate(
                    db,
                    old["responsibility"],
                    "Issue identity changed; explicit relink required",
                )
                return None
            if old:
                key = old["responsibility"]
                if migrate and migrate != key:
                    raise ValueError("Issue is already linked to another goal")
                current = self.store._get(db, key, OWNER)
                changed = revision != old["revision"]
                if changed:
                    self._invalidate(
                        db, key, "Issue changed; review and resume explicitly"
                    )
                    if valid_agent:
                        db.execute(
                            "UPDATE responsibilities SET agent=?,goal=? WHERE id=?",
                            (assigned[0], title, key),
                        )
                if not eligible:
                    if old["eligible"] or changed:
                        self._invalidate(db, key, reason)
                db.execute(
                    "UPDATE goal_sources SET title=?,body=?,revision=?,eligible=?,sync_at=?,sync_error=? WHERE responsibility=?",
                    (title, body, revision, int(eligible), now, reason, key),
                )
            else:
                if not eligible:
                    return None
                if migrate:
                    current = self.store._get(db, migrate, OWNER)
                    if (
                        current["agent"] != assigned[0]
                        or current["status"] == "cancelled"
                        or db.execute(
                            "SELECT 1 FROM goal_sources WHERE responsibility=?",
                            (migrate,),
                        ).fetchone()
                    ):
                        raise ValueError(
                            "Legacy goal must belong to this agent and be unlinked/noncancelled"
                        )
                    key = migrate
                    db.execute(
                        "UPDATE responsibilities SET goal=?,status='paused',phase='idle',checkpoint='{}',run_number=run_number+1,error='' WHERE id=?",
                        (title, key),
                    )
                else:
                    if (
                        db.execute(
                            "SELECT count(*) FROM responsibilities WHERE owner=? AND status!='cancelled'",
                            (OWNER,),
                        ).fetchone()[0]
                        >= 20
                        or db.execute(
                            "SELECT count(*) FROM responsibilities"
                        ).fetchone()[0]
                        >= 1000
                    ):
                        raise ValueError("Responsibility capacity reached")
                    key = str(uuid4())
                    db.execute(
                        "INSERT INTO responsibilities(id,owner,agent,goal,interval_seconds,status,phase,next_at) VALUES(?,?,?,?,?,'paused','idle',?)",
                        (key, OWNER, assigned[0], title, interval, now),
                    )
                db.execute(
                    "INSERT INTO goal_sources(responsibility,repo,number,issue_id,title,body,revision,mode,eligible,sync_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (key, repo, number, identity, title, body, revision, mode, 1, now),
                )
        return self.enrich(self.store.get(key))

    def validate(self, key):
        source = self.source(key)
        if not source:
            return True
        try:
            self.allowed(source["repo"])
            self.ingest(
                source["repo"], self.github.issue(source["repo"], source["number"])
            )
            refreshed = self.source(key)
            return bool(refreshed["eligible"])
        except (GitHubUnavailable, ValueError):
            with self.service.policy.locked(), self.store.db._transaction() as db:
                self._invalidate(
                    db, key, "Cannot verify GitHub goal; fix sync and resume explicitly"
                )
            return False

    def sync(self, force=False):
        if not force and self.store.clock() < self.next_sync:
            return
        with self.service.policy.locked(), self.lock:
            self.next_sync = self.store.clock() + 300
            for item in self.settings()["repositories"]:
                if not item["enabled"]:
                    continue
                repo = item["repository"]
                try:
                    issues = self.github.issues(repo)
                    with self.store.db._transaction() as db:
                        tracked = db.execute(
                            "SELECT number FROM goal_sources WHERE repo=? UNION SELECT number FROM goal_repo_attention WHERE repo=?",
                            (repo, repo),
                        ).fetchall()
                    found = {i["number"] for i in issues}
                    # Fetch individual tracked issues absent from the open/flagged snapshot.
                    issues += [
                        self.github.issue(repo, r["number"])
                        for r in tracked
                        if r["number"] not in found
                    ]
                    for issue in issues:
                        self.ingest(repo, issue)
                    with self.store.db._transaction() as db:
                        db.execute(
                            "INSERT OR REPLACE INTO goal_repo_sync VALUES(?,?,?)",
                            (repo, self.store.clock(), ""),
                        )
                except (GitHubUnavailable, ValueError):
                    with self.store.db._transaction() as db:
                        db.execute(
                            "UPDATE goal_sources SET sync_error=? WHERE repo=?",
                            ("GitHub sync unavailable; cached goals preserved", repo),
                        )
                        db.execute(
                            "INSERT OR REPLACE INTO goal_repo_sync VALUES(?,?,?)",
                            (
                                repo,
                                self.store.clock(),
                                "GitHub sync unavailable; verify credentials, issue labels and repository access",
                            ),
                        )

    def operations(self, agent=""):
        with self.store.db._transaction() as db:
            rows = db.execute(
                "SELECT * FROM goal_repo_operations WHERE (?='' OR agent=?) ORDER BY rowid DESC LIMIT 100",
                (agent, agent),
            ).fetchall()
        return [
            {
                k: json.loads(r[k]) if k == "result" else r[k]
                for k in (
                    "id",
                    "agent",
                    "repo",
                    "kind",
                    "status",
                    "approval_id",
                    "result",
                    "error",
                )
            }
            for r in rows
        ]

    def submit(self, *, agent, repo, kind, payload, request_id):
        if agent not in self.agents() or not _AGENT.fullmatch(agent):
            raise ValueError("Unknown agent")
        repo = self.allowed(repo or self.settings(agent)["default_repository"])
        try:
            request_id = str(__import__("uuid").UUID(request_id))
        except (ValueError, TypeError):
            raise ValueError("request_id must be a UUID") from None
        if kind not in ("create", "link", "complete"):
            raise ValueError("Unknown goal operation")
        if payload.get("mode", "recurring") not in ("recurring", "finite"):
            raise ValueError("Invalid goal mode")
        if (
            type(payload.get("interval_seconds", 3600)) is not int
            or not 300 <= payload.get("interval_seconds", 3600) <= 604800
        ):
            raise ValueError("Invalid schedule interval")
        if kind == "create" and (
            not isinstance(payload.get("title"), str)
            or not 1 <= len(payload["title"].strip()) <= 256
            or not isinstance(payload.get("body", ""), str)
            or len(payload.get("body", "")) > 60000
        ):
            raise ValueError("Invalid issue title/body")
        if kind == "link" and (
            type(payload.get("number")) is not int or payload["number"] < 1
        ):
            raise ValueError("Invalid issue number")
        if payload.get("responsibility"):
            row = self.store.get(payload["responsibility"])
            if row["agent"] != agent or row["status"] == "cancelled":
                raise ValueError("Goal belongs to another agent or is cancelled")
            source = self.source(row["id"])
            if kind == "complete" and (
                not source or source["repo"] != repo or source["mode"] != "finite"
            ):
                raise ValueError("Only a linked finite goal can be completed")
            if kind == "complete":
                with self.store.db._transaction() as db:
                    previous = db.execute(
                        "SELECT payload FROM goal_repo_operations WHERE id=?",
                        (request_id,),
                    ).fetchone()
                snapshot = json.loads(previous["payload"]) if previous else {}
                payload = {
                    **payload,
                    "expected_issue_id": snapshot.get(
                        "expected_issue_id", source["issue_id"]
                    ),
                    "expected_revision": snapshot.get(
                        "expected_revision", source["revision"]
                    ),
                }
            if kind in ("create", "link") and source:
                raise ValueError("Goal is already linked")
        elif kind == "complete":
            raise ValueError("A responsibility is required")
        if kind == "link" and payload.get("responsibility"):
            with self.store.db._transaction() as db:
                linked = db.execute(
                    "SELECT responsibility FROM goal_sources WHERE repo=? AND number=?",
                    (repo, payload["number"]),
                ).fetchone()
            if linked and linked["responsibility"] != payload["responsibility"]:
                raise ValueError("Issue is already linked to another goal")
        encoded = json.dumps(payload, sort_keys=True)
        if len(encoded) > 48000:
            raise ValueError("Issue request exceeds the bounded approval payload")
        with self.store.db._transaction() as db:
            old = db.execute(
                "SELECT * FROM goal_repo_operations WHERE id=?", (request_id,)
            ).fetchone()
            if old and (old["agent"], old["repo"], old["kind"], old["payload"]) != (
                agent,
                repo,
                kind,
                encoded,
            ):
                raise ValueError("Request ID already used for a different operation")
            if not old:
                if (
                    db.execute(
                        "SELECT count(*) FROM goal_repo_operations WHERE status='pending'"
                    ).fetchone()[0]
                    >= 20
                ):
                    raise ValueError("Pending repository operation limit reached (20)")
                if (
                    db.execute("SELECT count(*) FROM goal_repo_operations").fetchone()[
                        0
                    ]
                    >= 1000
                ):
                    raise ValueError("Repository operation audit capacity reached")
                db.execute(
                    "INSERT INTO goal_repo_operations(id,agent,repo,kind,payload) VALUES(?,?,?,?,?)",
                    (request_id, agent, repo, kind, encoded),
                )
        # Explicit authenticated user opt-in mirrors responsibility resume.
        self.service.policy.set_enabled(True)
        self.process_operations(request_id)
        return next(r for r in self.operations(agent) if r["id"] == request_id)

    def process_operations(self, only=None):
        with self.service.policy.locked(), self.lock:
            with self.store.db._transaction() as db:
                rows = db.execute(
                    "SELECT * FROM goal_repo_operations WHERE status='pending' AND (? IS NULL OR id=?) ORDER BY rowid LIMIT 20",
                    (only, only),
                ).fetchall()
            if rows and not only:
                keys = [r["id"] for r in rows]
                index = (
                    (keys.index(self.last_operation) + 1) % len(rows)
                    if self.last_operation in keys
                    else 0
                )
                rows = [rows[index]]
            for row in rows:
                self.last_operation = row["id"]
                payload = json.loads(row["payload"])
                source = (
                    self.source(payload.get("responsibility", ""))
                    if row["kind"] == "complete"
                    else None
                )
                number = payload.get("number", source["number"] if source else None)
                resource = f'/repos/{row["repo"]}/issues' + (
                    f"/{number}" if number else ""
                )
                action = Action(
                    row["agent"],
                    "repository.modify",
                    "github.com",
                    resource,
                    json.dumps(
                        {"operation_id": row["id"], "kind": row["kind"], **payload}
                    ),
                )

                def preflight():
                    try:
                        self.allowed(row["repo"])
                        if row["agent"] not in self.agents():
                            return False
                        if payload.get("responsibility"):
                            current = self.store.get(payload["responsibility"])
                            if (
                                current["agent"] != row["agent"]
                                or current["status"] == "cancelled"
                            ):
                                return False
                        if row["kind"] == "complete":
                            if not self.validate(payload["responsibility"]):
                                return False
                            current_source = self.source(payload["responsibility"])
                            if (
                                current_source["issue_id"],
                                current_source["revision"],
                            ) != (
                                payload["expected_issue_id"],
                                payload["expected_revision"],
                            ):
                                return False
                        return True
                    except (ValueError, KeyError):
                        return False

                def adapter(approved):
                    # Arguments come only from the persisted, fingerprinted action.
                    args = json.loads(approved.arguments_json)
                    repo = row["repo"]
                    if args["kind"] == "complete":
                        if not self.validate(args["responsibility"]):
                            raise ValueError("Issue is no longer eligible")
                        current_source = self.source(args["responsibility"])
                        if (current_source["issue_id"], current_source["revision"]) != (
                            args["expected_issue_id"],
                            args["expected_revision"],
                        ):
                            raise ValueError(
                                "Issue changed since completion was requested"
                            )
                        issue = self.github.request(
                            "PATCH", resource, {"state": "closed"}
                        )
                        self.ingest(repo, issue)
                        return {
                            "url": issue["html_url"],
                            "responsibility": args["responsibility"],
                        }
                    for label in ("always-on", "agent:" + row["agent"]):
                        self.github.ensure_label(repo, label)
                    if args["kind"] == "create":
                        issue = self.github.request(
                            "POST",
                            resource,
                            {
                                "title": args["title"],
                                "body": args.get("body", ""),
                                "labels": ["always-on", "agent:" + row["agent"]],
                            },
                        )
                    else:
                        issue = self.github.issue(repo, args["number"])
                        if "pull_request" in issue or issue["state"] != "open":
                            raise ValueError("Link an open issue, not a pull request")
                        assignments = [
                            l["name"]
                            for l in issue["labels"]
                            if l["name"].startswith("agent:")
                        ]
                        if any(
                            l["name"] in self.agents()
                            or l["name"] in ("in-progress", "queued")
                            or l["name"].endswith(":in-progress")
                            for l in issue["labels"]
                        ):
                            raise ValueError(
                                "Remove normal issue queue labels before linking an Always-On goal"
                            )
                        if assignments and assignments != ["agent:" + row["agent"]]:
                            raise ValueError(
                                "Issue already belongs to another or ambiguous agent"
                            )
                        self.github.request(
                            "POST",
                            resource + "/labels",
                            {"labels": ["always-on", "agent:" + row["agent"]]},
                        )
                        issue = self.github.issue(repo, args["number"])
                    linked = self.ingest(
                        repo,
                        issue,
                        interval=args.get("interval_seconds", 3600),
                        mode=args.get("mode", "recurring"),
                        migrate=args.get("responsibility"),
                    )
                    if not linked:
                        raise ValueError(
                            "Issue could not be linked; inspect operation before retrying"
                        )
                    with self.store.db._transaction() as db:
                        db.execute(
                            "UPDATE goal_sources SET mode=? WHERE responsibility=?",
                            (args.get("mode", "recurring"), linked["id"]),
                        )
                        db.execute(
                            "UPDATE responsibilities SET interval_seconds=?,status=CASE WHEN status='cancelled' THEN status ELSE 'paused' END,phase='idle',checkpoint='{}',run_number=run_number+1 WHERE id=?",
                            (args.get("interval_seconds", 3600), linked["id"]),
                        )
                    return {"url": issue["html_url"], "responsibility": linked["id"]}

                try:
                    if not preflight():
                        outcome = {"status": "invalidated"}
                    else:
                        outcome = self.service.execute(
                            action,
                            responsibility=payload.get(
                                "responsibility", "repo-operation:" + row["id"]
                            ),
                            intent_key="github-goal:" + row["id"],
                            adapter=adapter,
                            preflight=preflight,
                            summary=f'{row["kind"].capitalize()} Always-On issue in {row["repo"]} for {row["agent"]}',
                            details=action.arguments_json,
                        )
                    status = outcome["status"]
                    if status in ("approved", "pending", "paused", "rule_pending"):
                        status = "pending"
                    with self.store.db._transaction() as db:
                        db.execute(
                            "UPDATE goal_repo_operations SET status=?,approval_id=COALESCE(?,approval_id),result=? WHERE id=?",
                            (
                                status,
                                outcome.get("approval_id"),
                                json.dumps(outcome.get("result", {})),
                                row["id"],
                            ),
                        )
                except Exception:
                    with self.store.db._transaction() as db:
                        db.execute(
                            "UPDATE goal_repo_operations SET status='uncertain',error=? WHERE id=?",
                            (
                                "External operation interrupted; inspect GitHub and approval history before retrying",
                                row["id"],
                            ),
                        )


def create_repository_router(goals, authenticate):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, ConfigDict, Field

    router = APIRouter(prefix="/api/v1/autonomy", tags=["Always-On repositories"])

    class Repo(BaseModel):
        model_config = ConfigDict(extra="forbid")
        repository: str
        enabled: bool = True

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        repositories: list[Repo] = Field(max_length=20)
        default_repository: str = ""

    class Operation(BaseModel):
        model_config = ConfigDict(extra="forbid")
        agent: str
        repository: str = ""
        kind: str
        request_id: str
        title: str = Field(default="", max_length=256)
        body: str = Field(default="", max_length=60000)
        number: int | None = None
        responsibility: str | None = None
        interval_seconds: int = Field(default=3600, ge=300, le=604800)
        mode: str = "recurring"

    def guarded(auth, agent, call):
        try:
            principal(auth)
        except PermissionError:
            raise HTTPException(403, "Authenticated API account required") from None
        if agent and agent not in goals.agents():
            raise HTTPException(400, "Unknown agent")
        try:
            return call()
        except KeyError:
            raise HTTPException(404, "Not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except GitHubUnavailable as exc:
            raise HTTPException(503, str(exc))

    @router.get("/repositories")
    def settings(agent: str = "", auth=Depends(authenticate)):
        return guarded(auth, agent, lambda: goals.settings(agent))

    @router.put("/repositories")
    def configure(body: Settings, agent: str = "", auth=Depends(authenticate)):
        return guarded(
            auth,
            agent,
            lambda: goals.configure(
                [r.model_dump() for r in body.repositories],
                body.default_repository,
                agent,
            ),
        )

    @router.post("/repositories/sync")
    def sync(agent: str = "", auth=Depends(authenticate)):
        return guarded(auth, agent, lambda: (goals.sync(True), {"synced": True})[1])

    @router.get("/repository-operations")
    def operations(agent: str = "", auth=Depends(authenticate)):
        return guarded(auth, agent, lambda: {"operations": goals.operations(agent)})

    @router.post("/repository-operations")
    def operation(body: Operation, agent: str = "", auth=Depends(authenticate)):
        def submit():
            if agent and body.agent != agent:
                raise ValueError("Operation belongs to a different agent")
            data = body.model_dump(
                exclude={"agent", "repository", "kind", "request_id"}, exclude_none=True
            )
            return goals.submit(
                agent=body.agent,
                repo=body.repository,
                kind=body.kind,
                payload=data,
                request_id=body.request_id,
            )

        return guarded(auth, agent, submit)

    return router
