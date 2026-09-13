"""One-time sealed preference evaluation on fixed human-labeled candidate pairs."""

import uuid

from mimicry.loop.learning import pair_features
from mimicry.loop.models import now
from mimicry.loop.writing import near_same


async def evaluate_sealed(service, owner, policy_ids: list[str]) -> dict:
    if not 2 <= len(set(policy_ids)) <= 4:
        raise ValueError("Compare 2–4 different frozen evaluator versions.")
    policies = [service.store.get(owner, "policy", id_) for id_ in policy_ids]
    if any(p.get("version") != 2 for p in policies):
        raise ValueError("Compare v2 policies; old weighted policies cannot use this evaluator.")
    if len({(p["model"], p["output_format"]) for p in policies}) != 1:
        raise ValueError("Compare policies with the same pinned model and output format.")
    events = service.store.list(owner, "feedback")
    exposed = [e for e in events if e["partition"] != "sealed"]
    consumed = {g for r in service.store.list(owner, "sealed_evaluation")
                for g in r["intent_groups"]}
    groups = {}
    for event in events:
        if (event["partition"] != "sealed" or event["provenance"] != "human"
                or not event["style_eligible"] or event["intent_group"] in consumed
                or event["output_format"] != policies[0]["output_format"]
                or any(near_same(event["source_text"], e["source_text"]) for e in exposed)):
            continue
        groups.setdefault(event["intent_group"], event)
    if not groups:
        raise ValueError("No unused independent sealed human preferences are available.")
    groups = dict(list(groups.items())[:40])
    report = {"id": uuid.uuid4().hex, "created_at": now(), "status": "running",
              "policy_ids": policy_ids, "intent_groups": list(groups),
              "results": {}, "purpose": "Independent fixed-pair preference evaluation",
              "note": "No fitting or promotion. Small samples do not establish superiority."}
    # Consume cases before the first measurement; interrupted evaluations cannot be cherry-picked.
    service.store.reserve_evaluation(owner, report)
    api = service.provider(owner, service.store.output(owner, report["id"]))
    try:
        for policy in policies:
            rows = []
            for i, event in enumerate(groups.values()):
                preferred, other = await pair_features(api, event, policy, f"{policy['id']}_{i}")
                rows.append({"event_id": event["id"],
                             "preferred_passed": preferred["checks"]["passed"],
                             "other_passed": other["checks"]["passed"],
                             "preferred": preferred, "other": other})
            report["results"][policy["id"]] = {
                "rows": rows, "pairs": len(rows),
                "preferred_only_passed": sum(r["preferred_passed"] and not r["other_passed"]
                                             for r in rows),
                "preferred_rejected": sum(not r["preferred_passed"] for r in rows),
                "both_passed": sum(r["preferred_passed"] and r["other_passed"] for r in rows),
            }
        report["status"] = "complete"
    except Exception as error:
        report["status"] = "partial"
        report["error"] = type(error).__name__
    report["calls"] = api.calls
    service.store.put(owner, "sealed_evaluation", report, replace=True)
    return report
