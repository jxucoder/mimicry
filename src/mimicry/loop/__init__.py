"""Public app-independent API for personal writing and question-learning loops."""

from __future__ import annotations

from pathlib import Path

import httpx

from mimicry.engine import ROOT, settings
from mimicry.loop.evaluation import evaluate_sealed
from mimicry.loop.learning import (
    propose_learning,
    record_feedback,
    suspend_contradicted,
    validate_learning,
)
from mimicry.loop.models import check_context, digest, now
from mimicry.loop.provider import LoopProviders
from mimicry.loop.store import LoopStore
from mimicry.loop.writing import run_writing


class LoopService:
    """Resolve owner IDs from the app's authenticated session, not request form input."""

    def __init__(self, *, db_path: Path | None = None, config: dict | None = None,
                 client: httpx.AsyncClient | None = None, trace_sink=None):
        self.config = dict(settings() if config is None else config)
        self.store = LoopStore(db_path or ROOT / "runs" / "personal-loop" / "loop.sqlite3")
        self.client = client
        self.owned_client = client is None
        self.trace_sink = trace_sink

    async def __aenter__(self):
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=120)
        return self

    async def __aexit__(self, *_):
        if self.owned_client and self.client is not None:
            await self.client.aclose()
            self.client = None

    def provider(self, owner, output):
        for key in ("OPENAI_API_KEY", "TYPESAFE_API_KEY",
                    "STYLE_WRITER_MODEL", "STYLE_JUDGE_MODEL"):
            if not self.config.get(key):
                raise ValueError(f"Set {key} in the project .env or server environment.")
        if self.client is None:
            raise ValueError("Use async with LoopService() to manage the provider connection.")
        return LoopProviders(self.config, output, self.client, self.store, owner)

    def emit(self, owner, trace_id, event):
        record = self.store.put(owner, "trace", {"trace_id": trace_id, **event})
        if self.trace_sink is not None:
            try:
                self.trace_sink({"owner_hash": digest(owner)[:20], **record})
            except Exception:
                # Observability failures must not change which writing is selected.
                self.store.put(owner, "trace", {"trace_id": trace_id,
                                                "action": "TRACE_EXPORT_FAILED"})

    def add_evidence(self, owner: str, text: str, *, context: dict, kind="own",
                     provenance="user_selected", source_id="") -> dict:
        check_context(context)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 12000:
            raise ValueError("Evidence must contain 1–12,000 characters.")
        if kind not in ("own", "other_author", "ai_variant"):
            raise ValueError("Choose own, other_author, or ai_variant evidence.")
        if not isinstance(provenance, str) or not provenance.strip():
            raise ValueError("Record the evidence provenance.")
        record = {"id": digest({"text": text, "context": context, "kind": kind})[:24],
                  "text": text, "context": context, "kind": kind,
                  "provenance": provenance, "source_id": source_id, "created_at": now()}
        return self.store.put(owner, "evidence", record, replace=True)

    async def rewrite(self, owner: str, source_text: str, **kwargs) -> dict:
        return await run_writing(self, owner, source_text, **kwargs)

    async def feedback(self, owner: str, run_id: str, **kwargs) -> dict:
        return await record_feedback(self, owner, run_id, **kwargs)

    async def learn_from_feedback(self, owner: str, run_id: str, **kwargs) -> dict:
        """An edit proposes, supports, or suspends one small historical preference."""
        event = await self.feedback(owner, run_id, **kwargs)
        prior = [r for r in self.store.list(owner, "learning_turn") if r["id"] == event["id"]]
        if prior:
            return prior[0]
        result = {"id": event["id"], "feedback": event, "proposal": None,
                  "validation": None, "suspended": []}
        if (not event["style_eligible"] or event["provenance"] != "human"
                or event["partition"] == "sealed"):
            return result
        result["suspended"] = await suspend_contradicted(self, owner, event)
        active = self.store.active_policy(owner, self.config["STYLE_JUDGE_MODEL"],
                                          event["output_format"])
        shadow = [p for p in self.store.list(owner, "proposal")
                  if p["status"] == "shadow" and p["base_policy_id"] == active["id"]
                  and p["context"] == event["context"]]
        if shadow:
            result["proposal"] = shadow[-1]
            result["validation"] = await self.validate(owner, shadow[-1]["id"])
            result["proposal"] = self.store.get(owner, "proposal", shadow[-1]["id"])
        elif event["partition"] == "development" and not result["suspended"]:
            result["proposal"] = await self.learn(owner, event["id"])
        return self.store.put(owner, "learning_turn", result)

    async def learn(self, owner: str, event_id: str) -> dict:
        return await propose_learning(self, owner, event_id)

    async def validate(self, owner: str, proposal_id: str) -> dict:
        return await validate_learning(self, owner, proposal_id)

    async def evaluate(self, owner: str, policy_ids: list[str]) -> dict:
        return await evaluate_sealed(self, owner, policy_ids)

    def rollback(self, owner: str, policy_id: str, *, output_format="tweet") -> dict:
        current = self.store.active_policy(owner, self.config["STYLE_JUDGE_MODEL"], output_format)
        transitions = self.store.list(owner, "transition")
        activated = {t[k] for t in transitions for k in ("from", "to")}
        if policy_id not in activated or policy_id == current["id"]:
            raise ValueError("Rollback requires a previously active policy version.")
        target = self.store.get(owner, "policy", policy_id)
        if (target["output_format"] != output_format or target["model"] != current["model"]
                or target.get("version") != 2):
            raise ValueError("Cannot roll back to a different format or model.")
        transition = self.store.activate(owner, current["id"], target, {"kind": "rollback"})
        self.emit(owner, transition["id"], {"action": "ROLLBACK", "to": target["id"]})
        return transition

    def inspect(self, owner: str, *, output_format="tweet") -> dict:
        policy = self.store.active_policy(owner, self.config["STYLE_JUDGE_MODEL"], output_format)
        proposals = self.store.list(owner, "proposal")
        for proposal in proposals:
            proposal["currently_active"] = proposal.get("candidate_policy_id") == policy["id"]
        return {"active_policy": policy,
                "proposals": proposals,
                "validations": self.store.list(owner, "validation"),
                "transitions": self.store.list(owner, "transition"),
                "feedback_counts": {partition: sum(r["partition"] == partition
                    for r in self.store.list(owner, "feedback"))
                    for partition in ("development", "prospective", "sealed")}}


__all__ = ["LoopService", "LoopStore"]
