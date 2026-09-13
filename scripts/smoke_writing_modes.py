"""Compare all three writing modes using fictional text and real provider calls."""

import argparse
import asyncio
import json
from datetime import UTC, datetime

from mimicry.engine import ROOT
from mimicry.loop import LoopService
from mimicry.loop.models import WRITING_MODES

REFERENCE = """shipped the smaller parser. same output, fewer moving parts.
spent the morning debugging the wrong layer. the logs were right there.
new benchmark looks good. still want to see what happens with real traffic.
cut two features from the demo. now i can actually explain what it does."""
SOURCE = ("The parser is now accepting Unicode input and I plan to release it tomorrow. "
          "I tested it on the example files, but I haven't tested production traffic yet.")


async def smoke():
    root = ROOT / "runs" / "writing-modes" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root.mkdir(parents=True)
    report = {"source": SOURCE, "references": REFERENCE, "modes": [],
              "note": "Fictional smoke test. Model checks are not independent quality labels."}
    (root / "manifest.json").write_text(json.dumps(report, indent=2))
    async with LoopService(db_path=root / "loop.sqlite3") as service:
        for mode, label in WRITING_MODES.items():
            print(label, flush=True)
            result = await service.rewrite(
                "fictional-modes", SOURCE, references=REFERENCE if mode != "refine" else "",
                context={"language": "en", "purpose": "build_update"}, writing_mode=mode,
            )
            view = result["review_version"] if result["review_required"] else result["selected"]
            report["modes"].append({"mode": mode, "label": label, "status": result["status"],
                "review_required": result["review_required"], "display_version": view,
                "text": result["versions"][view]["text"], "calls": len(result["calls"]),
                "output_dir": result["output_dir"]})
            (root / "result.json").write_text(json.dumps(report, indent=2))
        lines = ["# Three writing modes", "", SOURCE, "", report["note"], ""]
        for row in report["modes"]:
            lines += ["## " + row["label"], "", row["text"], "",
                      f"Review required: {row['review_required']}; requests: {row['calls']}.", ""]
        (root / "report.md").write_text("\n".join(lines))
        print(json.dumps({"output": str(root), **report}, indent=2), flush=True)
        return all(row["status"] == "complete" for row in report["modes"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    if not asyncio.run(smoke()):
        raise SystemExit(1)
