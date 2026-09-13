"""Owner-scoped local records, atomic policy promotion, and reusable judgments."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from mimicry.loop.models import digest, now, seed_policy


class LoopStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    body TEXT NOT NULL, UNIQUE(owner, kind, id)
                );
                CREATE TABLE IF NOT EXISTS active (
                    owner TEXT NOT NULL, format TEXT NOT NULL, policy TEXT NOT NULL,
                    PRIMARY KEY(owner, format)
                );
            """)
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    @staticmethod
    def owner(owner: str) -> str:
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
            raise ValueError("A server-authenticated owner ID is required.")
        return owner

    def put(self, owner: str, kind: str, record: dict, *, replace=False) -> dict:
        owner = self.owner(owner)
        data = dict(record)
        data.setdefault("id", uuid.uuid4().hex)
        data.setdefault("created_at", now())
        command = "INSERT OR REPLACE" if replace else "INSERT"
        with self.connect() as db:
            db.execute(f"{command} INTO records(owner,kind,id,body) VALUES(?,?,?,?)",
                       (owner, kind, data["id"], json.dumps(data, ensure_ascii=False,
                                                          allow_nan=False)))
        return data

    def get(self, owner: str, kind: str, id_: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT body FROM records WHERE owner=? AND kind=? AND id=?",
                             (self.owner(owner), kind, id_)).fetchone()
        if row is None:
            raise ValueError(f"No {kind} record belongs to this owner and ID.")
        return json.loads(row[0])

    def list(self, owner: str, kind: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT body FROM records WHERE owner=? AND kind=? ORDER BY seq",
                              (self.owner(owner), kind)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def active_policy(self, owner: str, model: str, output_format: str) -> dict:
        self.owner(owner)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT policy FROM active WHERE owner=? AND format=?",
                             (owner, output_format)).fetchone()
            if row is None:
                policy = seed_policy(model, output_format)
                db.execute("INSERT INTO records(owner,kind,id,body) VALUES(?,?,?,?)",
                           (owner, "policy", policy["id"], json.dumps(policy)))
                db.execute("INSERT INTO active VALUES(?,?,?)",
                           (owner, output_format, policy["id"]))
            else:
                policy = json.loads(db.execute(
                    "SELECT body FROM records WHERE owner=? AND kind='policy' AND id=?",
                    (owner, row[0]),
                ).fetchone()[0])
                if policy.get("version") != 2:
                    # Scores learned under v1 are not executable v2 rules. Preserve history.
                    old_id = policy["id"]
                    policy = seed_policy(model, output_format)
                    db.execute("INSERT INTO records(owner,kind,id,body) VALUES(?,?,?,?)",
                               (owner, "policy", policy["id"], json.dumps(policy)))
                    db.execute("UPDATE active SET policy=? WHERE owner=? AND format=?",
                               (policy["id"], owner, output_format))
                    migration = {"id": uuid.uuid4().hex, "created_at": now(),
                                 "kind": "simplify_v2", "from": old_id, "to": policy["id"]}
                    db.execute("INSERT INTO records(owner,kind,id,body) VALUES(?,?,?,?)",
                               (owner, "transition", migration["id"], json.dumps(migration)))
        if policy["model"] != model:
            raise ValueError("The active evaluator uses a different pinned TypeSafe model.")
        return policy

    def activate(self, owner: str, expected_id: str, policy: dict, reason: dict) -> dict:
        """Compare-and-swap prevents stale learning jobs overwriting a newer policy."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE active SET policy=? WHERE owner=? AND format=? AND policy=?",
                (policy["id"], self.owner(owner), policy["output_format"], expected_id),
            ).rowcount
            if changed != 1:
                raise ValueError("The active policy changed; validate against the new version.")
            db.execute("INSERT OR IGNORE INTO records(owner,kind,id,body) VALUES(?,?,?,?)",
                       (owner, "policy", policy["id"], json.dumps(policy, allow_nan=False)))
            transition = {"id": uuid.uuid4().hex, "created_at": now(),
                          "from": expected_id, "to": policy["id"], **reason}
            db.execute("INSERT INTO records(owner,kind,id,body) VALUES(?,?,?,?)",
                       (owner, "transition", transition["id"], json.dumps(transition)))
        return transition

    def cache_get(self, owner: str, key: str):
        try:
            return self.get(owner, "judgment", key)["answer"]
        except ValueError:
            return None

    def reserve_evaluation(self, owner: str, report: dict):
        """Consume sealed groups atomically, including across worker processes."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for group in report["intent_groups"]:
                row = db.execute(
                    "SELECT id FROM records WHERE owner=? AND kind='sealed_group' AND id=?",
                    (self.owner(owner), group),
                ).fetchone()
                if row:
                    raise ValueError("A concurrent evaluation consumed these sealed groups.")
                db.execute("INSERT INTO records(owner,kind,id,body) VALUES(?,?,?,?)",
                           (owner, "sealed_group", group, json.dumps({"report_id": report["id"]})))
            db.execute("INSERT INTO records(owner,kind,id,body) VALUES(?,?,?,?)",
                       (owner, "sealed_evaluation", report["id"], json.dumps(report)))

    def cache_put(self, owner: str, key: str, answer: dict):
        self.put(owner, "judgment", {"id": key, "answer": answer}, replace=True)

    def output(self, owner: str, id_: str) -> Path:
        path = self.path.parent / "traces" / digest(self.owner(owner))[:20] / digest(id_)[:24]
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path
