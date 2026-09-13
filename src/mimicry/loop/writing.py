"""Write once, check explicit conditions, and repair once only when a condition fails."""

from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy
from difflib import SequenceMatcher

from mimicry.loop.models import (
    GATES,
    WRITING_MODES,
    check_context,
    decision,
    group_id,
    normalized,
    now,
    question_bank,
)
from mimicry.tweets import tweet_check


def relevant(records: list[dict], source: str, context: dict, limit: int) -> list[dict]:
    words = set(normalized(source).split())
    matches = [r for r in records if r["context"] == context]
    return sorted(matches, key=lambda r: (
        len(words & set(normalized(r.get("source_text", r.get("text", ""))).split())),
        r.get("created_at", ""),
    ), reverse=True)[:limit]


def near_same(a: str, b: str) -> bool:
    return SequenceMatcher(None, normalized(a), normalized(b), autojunk=False).ratio() >= 0.9


async def run_writing(service, owner: str, source_text: str, *, references="",
                      context=None, output_format="tweet", feed_context="",
                      intent_version=None, max_repairs=1, on_snapshot=None,
                      writing_mode="refine_personalize") -> dict:
    context = check_context(context or {"language": "en", "purpose": "general"})
    if not isinstance(writing_mode, str) or writing_mode not in WRITING_MODES:
        raise ValueError("Choose refine_personalize, personalize, or refine.")
    personalize = writing_mode != "refine"
    for name, text, minimum in (("source_text", source_text, 10),
                               ("references", references, 0), ("feed_context", feed_context, 0)):
        if not isinstance(text, str) or not minimum <= len(text.strip()) <= 12000:
            raise ValueError(f"{name} must contain {minimum}–12,000 characters.")
    if output_format not in ("tweet", "text"):
        raise ValueError("Choose tweet or text output.")
    if type(max_repairs) is not int or max_repairs not in (0, 1):
        raise ValueError("Choose zero or one repair.")
    evidence = service.store.list(owner, "evidence") if personalize else []
    own = relevant([r for r in evidence if r["kind"] == "own"], source_text, context, 6)
    if not personalize:
        references = ""
    elif not references:
        references = "\n\n".join(r["text"] for r in own)[:12000]
    if personalize and len(references.strip()) < 80:
        raise ValueError("Select at least 80 characters of your writing as style evidence.")
    contrasts = relevant([r for r in evidence if r["kind"] != "own"
                          and 0.4 <= len(r["text"]) / max(1, len(source_text)) <= 2.5],
                         source_text, context, 4)
    contrasts = [{k: r[k] for k in ("text", "kind")} for r in contrasts]
    policy = service.store.active_policy(owner, service.config["STYLE_JUDGE_MODEL"], output_format)
    bank = question_bank(policy, context, personalize=personalize)
    rules = [r for r in policy["rules"] if personalize and r["scope"] == context]
    id_ = uuid.uuid4().hex
    output = service.store.output(owner, id_)
    state = {"source_text": source_text, "references": references,
             "feed_context": feed_context, "output_format": output_format,
             "context": context, "contrasts": contrasts, "writing_mode": writing_mode}
    result = {
        "id": id_, "run_id": id_, "created_at": now(), "status": "running",
        "mode": "personal_loop", "loop_version": 2, **state, "state": state,
        "intent_version": intent_version or group_id(source_text),
        "intent_group": group_id(source_text), "evaluator": policy, "evaluator_id": policy["id"],
        "criteria": bank, "memory_ids": [r["id"] for r in rules],
        "evidence_ids": [r["id"] for r in own],
        "versions": {"original": {"text": source_text, "origin": "user_input"}},
        "selected": "original", "review_version": "original", "steps": [],
        "rejected_attempts": [],
        "budgets": {"drafts": 1, "max_repairs": max_repairs}, "review_required": True,
        "output_dir": str(output), "rating_note": "Model checks, not a style or authorship score.",
    }

    def save():
        service.store.put(owner, "run", result, replace=True)
        temporary = output / "result.json.tmp"
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        temporary.replace(output / "result.json")
        if on_snapshot:
            on_snapshot(deepcopy(result))

    def action(name, **details):
        event = {"action": name, "evaluator_id": policy["id"], **details}
        result["steps"].append(event)
        service.emit(owner, id_, event)

    api = service.provider(owner, output)
    save()

    async def check(version):
        candidate = result["versions"][version]
        text = candidate["text"]
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 12000:
            raise ValueError("Writer output exceeded the bounded candidate size.")
        candidate["tweet_check"] = (tweet_check(text) if output_format == "tweet"
                                    else {"valid": bool(text.strip())})
        candidate["format_valid"] = candidate["tweet_check"]["valid"]
        action("CHECKING", version=version)
        save()
        measured = await api.measure(state | {"candidate": text}, bank, policy["model"],
                                     version + "_check")
        candidate.update(measured)
        verdict = decision(measured, policy, context, candidate["format_valid"],
                           personalize=personalize)
        candidate.update({"checks": verdict, "eligible": verdict["passed"]})
        # Keep the last factually and instructionally acceptable draft available for review.
        # Never replace it with a repair that introduces a new hard failure.
        if verdict["hard_passed"]:
            result["selected"] = version
        if not set(verdict["failed"]) & {*GATES, "tweet_length"}:
            result["review_version"] = version
        if not verdict["passed"]:
            result["rejected_attempts"].append({"version": version, **verdict})
        action("CHECK", version=version, **verdict)
        save()
        return verdict

    try:
        action("WRITE", rules=[r["id"] for r in rules])
        save()
        draft = await api.write(state | {"current_text": source_text, "preferences": rules},
                                "draft_write")
        result["versions"]["draft"] = {"text": draft, "origin": "writer", "phase": "rewrite",
                                         "based_on": "original"}
        save()
        verdict = await check("draft")
        # Ambiguity alone is not a reason to keep rewriting. A concrete failure is.
        if not verdict["passed"] and verdict["failed"] and max_repairs:
            failures = [{"id": k, "question": bank.get(k),
                         "rule": next((r for r in rules if r["id"] == k), None)}
                        for k in verdict["failed"]]
            feedback = {"failures": failures, "uncertain": verdict["uncertain"],
                        "tweet_check": result["versions"]["draft"]["tweet_check"]}
            action("REPAIR", failures=verdict["failed"], based_on="draft")
            save()
            repaired = await api.write(state | {"current_text": draft, "preferences": rules,
                                                "feedback": feedback}, "repair_write")
            result["versions"]["repair"] = {"text": repaired, "origin": "writer",
                                              "phase": "repair", "based_on": "draft"}
            save()
            if repaired.strip() == draft.strip():
                action("DUPLICATE", version="repair", duplicate_of="draft")
                result["stop_reason"] = "duplicate"
            else:
                verdict = await check("repair")
        selected = result["versions"][result["selected"]]
        result["review_required"] = not selected.get("eligible", False)
        result.setdefault("stop_reason", "checks_passed" if not result["review_required"]
                          else "review_required")
        result["status"] = "complete"
        action("REVIEW" if result["review_required"] else "ACCEPT", selected=result["selected"])
        action("STOP", reason=result["stop_reason"], selected=result["selected"])
    except asyncio.CancelledError:
        result.update(status="cancelled", stop_reason="cancelled", review_required=True)
        action("STOP", reason="cancelled")
        raise
    except Exception as error:
        result.update(status="partial", stop_reason="provider_or_validation_failure",
                      review_required=True,
                      error=f"{type(error).__name__}: loop interrupted; inspect the saved stage.")
        action("STOP", reason=result["stop_reason"])
    finally:
        result["calls"] = api.calls
        result["selected_eligible"] = result["versions"][result["selected"]].get("eligible", False)
        save()
        (output / "writing.txt").write_text(result["versions"][result["selected"]]["text"])
    return result
