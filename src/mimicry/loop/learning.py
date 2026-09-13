"""An edit proposes a scoped rule; a later edit and an exception check can enable it."""

from __future__ import annotations

import uuid
from copy import deepcopy
from difflib import SequenceMatcher

from mimicry.loop.models import (
    MAX_RULES,
    NO,
    YES,
    check_context,
    decision,
    digest,
    group_id,
    now,
    question_bank,
    seal_policy,
)
from mimicry.loop.provider import ATTRIBUTION_QUESTIONS
from mimicry.loop.writing import near_same
from mimicry.tweets import tweet_check


async def record_feedback(service, owner: str, run_id: str, *, against_version: str,
                          intent_version: str, edited_text=None, chosen_version=None,
                          explanation="", intent_changed=False, partition="development",
                          provenance="human", event_id=None) -> dict:
    run = service.store.get(owner, "run", run_id)
    if event_id is not None and (not isinstance(event_id, str) or not 1 <= len(event_id) <= 200):
        raise ValueError("A feedback idempotency key must contain 1–200 characters.")
    if intent_version != run["intent_version"]:
        raise ValueError("This feedback belongs to an outdated intent version.")
    if partition not in ("development", "prospective", "sealed"):
        raise ValueError("Choose development, prospective, or sealed feedback.")
    if provenance not in ("human", "synthetic"):
        raise ValueError("Feedback provenance must be human or synthetic.")
    if (edited_text is None) == (chosen_version is None):
        raise ValueError("Supply either edited_text or chosen_version.")
    if against_version not in run["versions"] or (chosen_version is not None
                                                 and chosen_version not in run["versions"]):
        raise ValueError("The comparison must refer to versions actually shown in this run.")
    after = edited_text if edited_text is not None else run["versions"][chosen_version]["text"]
    if not isinstance(after, str) or not 1 <= len(after.strip()) <= 12000:
        raise ValueError("Edited text must contain 1–12,000 characters.")
    if not isinstance(explanation, str) or len(explanation) > 4000:
        raise ValueError("Feedback explanation must be text within 4,000 characters.")
    if type(intent_changed) is not bool:
        raise ValueError("intent_changed must be a boolean.")
    before = run["versions"][against_version]["text"]
    # Partition whole intent families, including nearly identical drafts, together.
    group = run["intent_group"]
    previous = service.store.list(owner, "feedback")
    related = [r for r in previous if near_same(r["source_text"], run["source_text"])]
    if related:
        if any(r["partition"] != partition for r in related):
            raise ValueError("Variants of one intent must remain in the same evaluation partition.")
        group = related[0]["intent_group"]
    event = {
        "id": event_id or uuid.uuid4().hex, "created_at": now(), "run_id": run_id,
        "source_text": run["source_text"], "intent_group": group,
        "intent_version": intent_version, "context": run["context"],
        "output_format": run["output_format"], "evaluator_id": run["evaluator_id"],
        "writing_mode": run.get("writing_mode", "refine_personalize"),
        "state": run["state"], "before": before, "after": after,
        "against_version": against_version, "chosen_version": chosen_version,
        "explanation": explanation, "intent_changed": intent_changed,
        "new_intent_version": group_id(after) if intent_changed else None,
        "partition": partition, "provenance": provenance,
        "diff": [{"operation": op, "before": before[i:j], "after": after[k:end]}
                 for op, i, j, k, end in SequenceMatcher(None, before, after,
                                                       autojunk=False).get_opcodes()
                 if op != "equal"],
        "style_eligible": False, "attribution_status": "pending",
    }
    if event_id:
        existing = next((r for r in previous if r["id"] == event_id), None)
        if existing:
            fields = ("run_id", "before", "after", "explanation", "partition", "provenance",
                      "intent_changed")
            if any(existing[k] != event[k] for k in fields):
                raise ValueError("The feedback idempotency key was reused with different data.")
            return existing
    service.store.put(owner, "feedback", event)
    if run.get("writing_mode") == "refine":
        event["attribution_status"] = "refine_only"
    elif intent_changed or before.strip() == after.strip():
        event["attribution_status"] = "intent_changed" if intent_changed else "no_change"
    else:
        api = service.provider(owner, service.store.output(owner, event["id"]))
        try:
            response = await api.measure({"source_text": run["source_text"],
                                          "before": before, "after": after,
                                          "explanation": explanation},
                                         ATTRIBUTION_QUESTIONS, run["evaluator"]["model"],
                                         "attribute_edit")
            event["attribution"] = response
            event["style_eligible"] = all(response["features"][k] >= 0.85
                                          for k in ATTRIBUTION_QUESTIONS)
            event["attribution_status"] = ("style" if event["style_eligible"]
                                           else "mixed_or_uncertain")
        except Exception as error:
            event["attribution_status"] = "failed"
            event["error"] = type(error).__name__
        event["calls"] = api.calls
    service.store.put(owner, "feedback", event, replace=True)
    service.emit(owner, event["id"], {"action": "RECORD_FEEDBACK", "run_id": run_id,
                                     "style_eligible": event["style_eligible"],
                                     "partition": partition})
    return event


def compile_proposal(base: dict, raw: dict, context: dict, event_id: str) -> dict:
    if raw["operation"] != "add":
        raise ValueError("No actionable preference was proposed.")
    check_context(context)
    for key in ("name", "preference", "when", "exception", "exception_request",
                "exception_candidate"):
        if not isinstance(raw.get(key), str) or not 1 <= len(raw[key].strip()) <= 1000:
            raise ValueError("A rule requires bounded preference, scope and exception fields.")
    if len(base["rules"]) >= MAX_RULES:
        raise ValueError("Keep at most five active preferences; retire one before adding another.")
    identity = {k: raw[k].strip() for k in ("preference", "when", "exception")}
    rule = {"id": "rule_" + digest(identity | {"scope": context})[:16], **identity,
            "scope": context, "origin": event_id, "name": raw["name"].strip()}
    if any(r["id"] == rule["id"] for r in base["rules"]):
        raise ValueError("This preference already exists.")
    return seal_policy({**deepcopy(base), "parent_id": base["id"], "created_at": now(),
                        "rules": [*base["rules"], rule]})


async def assess(api, state: dict, text: str, policy: dict, stage: str) -> dict:
    personalize = state.get("writing_mode") != "refine"
    measured = await api.measure(state | {"candidate": text},
                                 question_bank(policy, state["context"], personalize=personalize),
                                 policy["model"], stage)
    valid = state["output_format"] != "tweet" or tweet_check(text)["valid"]
    return {**measured, "checks": decision(measured, policy, state["context"], valid,
                                          personalize=personalize)}


async def pair_features(api, event: dict, policy: dict, stage: str) -> tuple[dict, dict]:
    after = await assess(api, event["state"], event["after"], policy, stage + "_after")
    before = await assess(api, event["state"], event["before"], policy, stage + "_before")
    return after, before


def supports(rule_id: str, after: dict, before: dict) -> bool:
    return (after["checks"]["passed"] and before["checks"]["hard_passed"]
            and before["features"][rule_id + "_applies"] >= YES
            and before["features"][rule_id + "_violated"] >= YES
            and after["features"][rule_id + "_applies"] >= YES
            and after["features"][rule_id + "_violated"] <= NO)


async def propose_learning(service, owner: str, event_id: str) -> dict:
    event = service.store.get(owner, "feedback", event_id)
    if (not event["style_eligible"] or event["partition"] != "development"
            or event["provenance"] != "human"):
        raise ValueError("Question learning requires a human development style preference.")
    existing = [p for p in service.store.list(owner, "proposal")
                if p["event_id"] == event_id and p.get("loop_version") == 2]
    if existing:
        return existing[-1]
    base = service.store.active_policy(owner, service.config["STYLE_JUDGE_MODEL"],
                                       event["output_format"])
    # One pending idea per context prevents a fresh edit spawning a forest of hypotheses.
    pending = [p for p in service.store.list(owner, "proposal")
               if p["status"] == "shadow" and p["context"] == event["context"]
               and p["base_policy_id"] == base["id"]]
    if pending:
        return pending[-1]
    proposal = {"id": uuid.uuid4().hex, "event_id": event_id, "created_at": now(),
                "loop_version": 2,
                "base_policy_id": base["id"], "context": event["context"], "status": "proposed"}
    service.store.put(owner, "proposal", proposal)
    api = service.provider(owner, service.store.output(owner, proposal["id"]))
    try:
        raw = await api.propose({"correction": {k: event[k] for k in
            ("source_text", "before", "after", "explanation", "context")},
            "existing_rules": base["rules"]}, "propose_preference")
        proposal["raw"] = raw
        if raw["operation"] == "none":
            proposal["status"] = "no_change"
        else:
            candidate = compile_proposal(base, raw, event["context"], event_id)
            rule_id = candidate["rules"][-1]["id"]
            after, before = await pair_features(api, event, candidate, "trigger")
            exception_state = event["state"] | {
                "source_text": event["source_text"] + "\n\n" + raw["exception_request"],
                "feed_context": "",
            }
            exception = await assess(api, exception_state, raw["exception_candidate"],
                                     candidate, "exception")
            passed = (supports(rule_id, after, before) and exception["checks"]["hard_passed"]
                      and exception["features"][rule_id + "_applies"] <= NO)
            proposal["self_check"] = {"passed": passed, "before": before, "after": after,
                                      "exception": exception,
                                      "note": "Trigger replay and synthetic exception only."}
            if passed:
                service.store.put(owner, "policy", candidate)
                proposal.update(status="shadow", candidate_policy_id=candidate["id"],
                                rule_id=rule_id, frozen_at=now())
            else:
                proposal["status"] = "rejected"
    except Exception as error:
        proposal.update(status="failed", error=type(error).__name__)
    proposal["calls"] = api.calls
    service.store.put(owner, "proposal", proposal, replace=True)
    service.emit(owner, proposal["id"], {"action": "PROPOSE_QUESTION",
                                       "status": proposal["status"]})
    return proposal


async def validate_learning(service, owner: str, proposal_id: str) -> dict:
    proposal = service.store.get(owner, "proposal", proposal_id)
    if proposal["status"] != "shadow":
        raise ValueError("Only a shadow preference can be validated.")
    base = service.store.get(owner, "policy", proposal["base_policy_id"])
    candidate = service.store.get(owner, "policy", proposal["candidate_policy_id"])
    active = service.store.active_policy(owner, base["model"], base["output_format"])
    if active["id"] != base["id"]:
        raise ValueError("This proposal is stale; the active preferences changed.")
    trigger = service.store.get(owner, "feedback", proposal["event_id"])
    groups = {}
    for e in service.store.list(owner, "feedback"):
        if (e["style_eligible"] and e["provenance"] == "human" and e["partition"] != "sealed"
                and e["output_format"] == base["output_format"]
                and e["context"] == proposal["context"] and e["created_at"] > proposal["frozen_at"]
                and not near_same(e["source_text"], trigger["source_text"])):
            groups.setdefault(e["intent_group"], e)
    report = {"id": uuid.uuid4().hex, "proposal_id": proposal_id, "promoted": False,
              "status": "awaiting_another_edit", "rows": [],
              "note": "Two human edits plus a synthetic exception check; not generalization proof."}
    api = service.provider(owner, service.store.output(owner, report["id"]))
    try:
        for event in list(groups.values())[-10:]:
            after, before = await pair_features(api, event, candidate, event["id"])
            report["rows"].append({"event_id": event["id"], "after": after, "before": before,
                                   "supports": supports(proposal["rule_id"], after, before),
                                   "contradicts": (after["checks"]["hard_passed"]
                                       and proposal["rule_id"] in after["checks"]["failed"])})
        if any(r["contradicts"] for r in report["rows"]):
            report["status"] = "contradicted"
            proposal["status"] = "rejected"
        elif any(r["supports"] for r in report["rows"]):
            # Save the evidence before changing what the next writing request will use.
            report.update(status="passed", calls=api.calls)
            service.store.put(owner, "validation", report)
            service.store.activate(owner, base["id"], candidate,
                                   {"kind": "promotion", "proposal_id": proposal_id,
                                    "validation_id": report["id"]})
            report.update(status="passed", promoted=True)
            proposal["status"] = "active"
    except Exception as error:
        report.update(status="failed", error=type(error).__name__)
    report["calls"] = api.calls
    service.store.put(owner, "validation", report, replace=True)
    service.store.put(owner, "proposal", proposal, replace=True)
    service.emit(owner, report["id"], {"action": "VALIDATE_QUESTION", "status": report["status"],
                                      "promoted": report["promoted"]})
    return report


async def suspend_contradicted(service, owner: str, event: dict) -> list[str]:
    """A later human correction can stop a rule; it never loosens the fixed checks."""
    if (not event["style_eligible"] or event["provenance"] != "human"
            or event["partition"] == "sealed"):
        return []
    policy = service.store.active_policy(owner, service.config["STYLE_JUDGE_MODEL"],
                                         event["output_format"])
    if not any(r["scope"] == event["context"] for r in policy["rules"]):
        return []
    api = service.provider(owner, service.store.output(owner, event["id"] + "_suspend"))
    after = await assess(api, event["state"], event["after"], policy, "review_active_preferences")
    ids = [r["id"] for r in policy["rules"] if r["id"] in after["checks"]["failed"]]
    if ids and after["checks"]["hard_passed"]:
        updated = seal_policy({**policy, "parent_id": policy["id"], "created_at": now(),
                               "rules": [r for r in policy["rules"] if r["id"] not in ids]})
        service.store.activate(owner, policy["id"], updated,
                               {"kind": "suspend", "event_id": event["id"], "rule_ids": ids})
        service.emit(owner, event["id"], {"action": "SUSPEND_RULE", "rule_ids": ids})
        return ids
    return []
