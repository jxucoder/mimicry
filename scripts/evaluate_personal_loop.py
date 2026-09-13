"""Live v2 regression check. Fictional cases are engineering tests, not human evaluations."""

import argparse
import asyncio
import json
from datetime import UTC, datetime

from mimicry.engine import ROOT
from mimicry.loop import LoopService
from mimicry.loop.learning import assess, compile_proposal
from mimicry.loop.models import digest

CONTEXT = {"language": "en", "purpose": "build_update"}
REFERENCES = """shipped the smaller parser. same output, fewer moving parts.
spent the morning debugging the wrong layer. the logs were right there.
new benchmark looks good. still want to see what happens with real traffic.
cut two features from the demo. now i can actually explain what it does."""
# Frozen before requests. The first case is a known v1 failure, so this is regression evidence.
CASES = [
    {"id": "known_launch_order", "source": "Start by announcing that we shipped version 2. "
     "Then note that old plugins need an update.",
     "good": "we shipped version 2. old plugins need an update.",
     "bad": "old plugins need an update. we shipped version 2."},
    {"id": "new_result_first", "source": "Lead with the result: image uploads now take 2 seconds. "
     "Then mention that they used to take 9 seconds.",
     "good": "image uploads now take 2 seconds. they used to take 9 seconds.",
     "bad": "image uploads used to take 9 seconds. now they take 2 seconds."},
    {"id": "new_qualified_claim", "source": "The cache sometimes returns stale results. "
     "A fix is ready, but it has only been tested locally.",
     "good": "the cache sometimes returns stale results. fix is ready, only tested locally so far.",
     "bad": "the cache sometimes returns stale results. "
     "the fix is tested and ready for production."},
    {"id": "new_no_friction", "source": "We added dark mode today. It follows the system theme.",
     "good": "added dark mode today. follows the system theme.",
     "bad": "users hated the old theme. added dark mode today, and it follows the system theme."},
    {"id": "new_style_order", "source": "The queue was dropping messages. "
     "I added a retry limit, and the dropped-message count is now zero.",
     "good": "the queue was dropping messages. added a retry limit; the count is now zero.",
     "bad": "added a retry limit; the dropped-message count is now zero. "
     "the queue was dropping messages."},
    {"id": "new_exact_opening", "source": "Use the exact opening 'small win:'. "
     "The export failed on large files. I reduced peak memory and it now completes.",
     "good": "small win: the export failed on large files. reduced peak memory; it now completes.",
     "bad": "the export failed on large files. reduced peak memory; it now completes."},
]


async def evaluate():
    root = ROOT / "runs" / "loop-v2" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root.mkdir(parents=True)
    manifest = {"cases": CASES, "references": REFERENCES, "context": CONTEXT,
                "labels": "Author-defined fictional regression cases, not human preferences.",
                "protocol": "One rule proposal, frozen pair checks, three full writing runs. "
                            "No tuning to results; report failures and review decisions."}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    async with LoopService(db_path=root / "loop.sqlite3") as service:
        owner = "fictional-v2-regression"
        base = service.inspect(owner)["active_policy"]
        (root / "proposal").mkdir()
        api = service.provider(owner, root / "proposal")
        raw = await api.propose({"correction": {
            "source_text": "The new model gained two points offline, "
                           "but production latency doubled. "
                           "I am keeping the baseline while I test compression.",
            "before": "new model gained two points offline. production latency doubled. "
                      "keeping the baseline while i test compression.",
            "after": "production latency doubled. the new model gained two points offline. "
                     "keeping the baseline while i test compression.",
            "explanation": "For build updates with a concrete problem or tradeoff, "
                           "lead with that friction, then the improvement or next step. "
                           "Preserve facts. Current opening requests take priority.",
            "context": CONTEXT}, "existing_rules": []}, "propose")
        (root / "proposal.json").write_text(json.dumps(raw, indent=2))
        policy = compile_proposal(base, raw, CONTEXT, "synthetic-probe")
        (root / "policy.json").write_text(json.dumps(policy, indent=2))
        # Isolated synthetic profile: this bypass is for probing a rule, never human promotion.
        service.store.activate(owner, base["id"], policy, {"kind": "synthetic_probe"})
        report = {"manifest_hash": digest(manifest), "models": {
            "writer": service.config["STYLE_WRITER_MODEL"], "judge": policy["model"]},
            "policy_id": policy["id"], "pairs": [], "writing": [],
            "note": "Engineering regression evidence. No GPT-superiority or human-style claim."}
        for case in CASES:
            print("Checking", case["id"], flush=True)
            state = {"source_text": case["source"], "references": REFERENCES, "context": CONTEXT,
                     "feed_context": "", "contrasts": [], "output_format": "tweet"}
            row = {"id": case["id"], "source": case["source"]}
            for side in ("good", "bad"):
                row[side] = {"text": case[side], **await assess(
                    api, state, case[side], policy, case["id"] + "_" + side)}
            row["expected_behavior"] = (row["good"]["checks"]["passed"]
                                        and not row["bad"]["checks"]["passed"])
            report["pairs"].append(row)
            (root / "result.json").write_text(json.dumps(report, indent=2))
        for case in (CASES[0], CASES[4], CASES[2]):
            print("Writing", case["id"], flush=True)
            r = await service.rewrite(owner, case["source"], references=REFERENCES, context=CONTEXT)
            report["writing"].append({"id": case["id"], "run_id": r["id"],
                "text": r["versions"][r["selected"]]["text"], "status": r["status"],
                "review_required": r["review_required"], "steps": r["steps"],
                "calls": len(r["calls"]), "output_dir": r["output_dir"]})
            (root / "result.json").write_text(json.dumps(report, indent=2))
        print("Injecting a wrong first draft; checking and repair remain live.", flush=True)
        make_provider = service.provider

        def injected_provider(owner_id, output):
            provider = make_provider(owner_id, output)
            live_write = provider.write

            async def injected_write(state, stage):
                if stage == "draft_write":
                    return CASES[0]["bad"]
                return await live_write(state, stage)
            provider.write = injected_write
            return provider

        service.provider = injected_provider
        injected = await service.rewrite(owner, CASES[0]["source"],
                                         references=REFERENCES, context=CONTEXT)
        service.provider = make_provider
        report["fault_injection"] = {
            "note": "First draft planted by harness. TypeSafe checks and Luna repair are live.",
            "initial": CASES[0]["bad"],
            "final": injected["versions"][injected["selected"]]["text"],
            "selected": injected["selected"], "review_required": injected["review_required"],
            "steps": injected["steps"], "output_dir": injected["output_dir"],
        }
        report["pairs_matching_expected_behavior"] = sum(r["expected_behavior"]
                                                         for r in report["pairs"])
        (root / "result.json").write_text(json.dumps(report, indent=2))
        lines = ["# Simplified loop: live regression check", "", report["note"], "",
                 f"Models: {report['models']}", "",
                 "| Case | Expected draft | Planted failure | Matched expectation |",
                 "| --- | --- | --- | --- |"]
        for row in report["pairs"]:
            lines.append(f"| {row['id']} | {row['good']['checks']} | "
                         f"{row['bad']['checks']} | {row['expected_behavior']} |")
        lines += ["", "## Generated rewrites", ""]
        for r in report["writing"]:
            lines += [f"### {r['id']}", "", r["text"], "",
                      f"Requests: {r['calls']}; review required: {r['review_required']}.", ""]
        lines += ["## Live repair after an injected bad draft", "",
                  report["fault_injection"]["note"], "",
                  "Before: " + report["fault_injection"]["initial"], "",
                  "After: " + report["fault_injection"]["final"], ""]
        (root / "report.md").write_text("\n".join(lines))
        print(json.dumps({"output": str(root), "pairs": report["pairs_matching_expected_behavior"],
                          "total": len(CASES), "writing": report["writing"]}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(evaluate())
