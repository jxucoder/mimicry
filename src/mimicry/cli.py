"""Rewrite an existing AI draft in the voice of personal writing samples."""

import argparse
import asyncio
from pathlib import Path

from mimicry.engine import rewrite
from mimicry.examples import DRAFT, PRESETS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=list(PRESETS), default="Dry humor")
    parser.add_argument("--reference-file", type=Path)
    parser.add_argument("--draft-file", type=Path)
    parser.add_argument("--max-revisions", type=int, choices=(1, 2), default=2)
    parser.add_argument("--format", choices=("text", "tweet"), default="text")
    args = parser.parse_args()
    references = args.reference_file.read_text() if args.reference_file else PRESETS[args.preset]
    source_text = args.draft_file.read_text() if args.draft_file else DRAFT
    result = asyncio.run(rewrite(
        references, source_text, max_revisions=args.max_revisions,
        output_format=args.format, progress=print
    ))
    if result["selected"]:
        print("\n" + result["versions"][result["selected"]]["text"])
    print("\n" + result.get("decision", ""))
    print("Saved to " + result["output_dir"])
    if result["status"] != "complete":
        raise SystemExit(result.get("error", "The run did not complete."))


if __name__ == "__main__":
    main()
