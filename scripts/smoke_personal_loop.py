"""Exercise live provider contracts using fictional text, without learning human preferences."""

import argparse
import asyncio
import json

from mimicry.engine import ROOT
from mimicry.loop import LoopService
from mimicry.loop.learning import compile_proposal

REFERENCE = """shipped the smaller parser. same output, fewer moving parts.

spent the morning debugging the wrong layer. the logs were right there.

new benchmark looks good. still want to see what happens with real traffic.

cut two features from the demo. now i can actually explain what it does."""
SOURCE = ("The new model gained two points offline, but production latency doubled. "
          "I am keeping the baseline while I test compression.")


async def smoke():
    root = ROOT / "runs" / "personal-loop-smoke"
    async with LoopService(db_path=root / "loop.sqlite3") as service:
        print("Running live Luna + TypeSafe writing loop on fictional text.", flush=True)
        seen = set()

        def progress(snapshot):
            for step in snapshot["steps"]:
                label = (step["action"], step.get("version"), step.get("target"))
                if label not in seen:
                    seen.add(label)
                    print("Action:", *[str(x) for x in label if x is not None], flush=True)

        result = await service.rewrite(
            "fictional-smoke-profile", SOURCE, references=REFERENCE,
            context={"language": "en", "purpose": "build_update"},
            max_repairs=1, on_snapshot=progress,
        )
        summary = {"status": result["status"], "run_id": result["id"],
                   "evaluator_id": result["evaluator_id"], "selected": result["selected"],
                   "selected_eligible": result["selected_eligible"],
                   "provider_calls": len(result["calls"]), "output_dir": result["output_dir"],
                   "note": "Fictional smoke test. No human preference or learning result."}
        if result["status"] == "complete":
            print("Checking a synthetic edit and the live question-proposal contract.", flush=True)
            edit = ("new model: +2 offline, 2x production latency. "
                    "keeping the baseline while i test compression.")
            event = await service.feedback(
                "fictional-smoke-profile", result["id"], against_version="original",
                intent_version=result["intent_version"], edited_text=edit,
                explanation="Prefer lowercase and compact, concrete updates.",
                provenance="synthetic",
            )
            summary["attribution_status"] = event["attribution_status"]
            api = service.provider("fictional-smoke-profile", root)
            raw = await api.propose({
                "correction": {"source_text": SOURCE, "before": SOURCE, "after": edit,
                               "explanation": event["explanation"], "context": result["context"]},
                "existing_rules": result["evaluator"]["rules"],
                "note": "Fictional contract smoke test, not a human preference observation.",
            }, "smoke_question_proposal")
            summary["proposal_operation"] = raw["operation"]
            if raw["operation"] != "none":
                compile_proposal(result["evaluator"], raw, result["context"], event["id"])
            summary["proposal_contract_valid"] = True
            summary["policy_unchanged"] = service.inspect(
                "fictional-smoke-profile")["active_policy"]["id"] == result["evaluator_id"]
        (root / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2), flush=True)
        return result["status"] == "complete"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True,
                        help="Call configured OpenAI and TypeSafe APIs")
    parser.parse_args()
    if not asyncio.run(smoke()):
        raise SystemExit(1)
