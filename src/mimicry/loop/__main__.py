"""Run, inspect, and teach the personal loop without the X application interface."""

import argparse
import asyncio
import json
from pathlib import Path

from mimicry.loop import LoopService
from mimicry.loop.models import WRITING_MODES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="Local profile; app uses authenticated X ID")
    parser.add_argument("--db", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("rewrite")
    write.add_argument("--draft-file", type=Path, required=True)
    write.add_argument("--reference-file", type=Path)
    write.add_argument("--feed-file", type=Path)
    write.add_argument("--language", default="en")
    write.add_argument("--purpose", default="build_update")
    write.add_argument("--format", choices=("tweet", "text"), default="tweet")
    write.add_argument("--repairs", type=int, choices=(0, 1), default=1)
    write.add_argument("--mode", choices=tuple(WRITING_MODES), default="refine_personalize")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("--run", required=True)
    feedback.add_argument("--against", required=True)
    feedback.add_argument("--intent-version", required=True)
    choice = feedback.add_mutually_exclusive_group(required=True)
    choice.add_argument("--edit-file", type=Path)
    choice.add_argument("--choose")
    feedback.add_argument("--explanation", default="")
    feedback.add_argument("--intent-changed", action="store_true")
    feedback.add_argument("--partition", choices=("development", "prospective", "sealed"),
                          default="development")
    feedback.add_argument("--event-id", help="Optional idempotency key")
    feedback.add_argument("--learn", action="store_true", help="Run the bounded learning turn")
    learn = commands.add_parser("learn")
    learn.add_argument("--event", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--proposal", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--policies", nargs="+", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--policy", required=True)
    rollback.add_argument("--format", choices=("tweet", "text"), default="tweet")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--format", choices=("tweet", "text"), default="tweet")
    evidence = commands.add_parser("evidence")
    evidence.add_argument("--file", type=Path, required=True,
                          help="JSON list of selected text/context/kind/provenance objects")
    args = parser.parse_args()

    async def run():
        async with LoopService(db_path=args.db) as service:
            if args.command == "rewrite":
                return await service.rewrite(
                    args.owner, args.draft_file.read_text(),
                    references=args.reference_file.read_text() if args.reference_file else "",
                    feed_context=args.feed_file.read_text() if args.feed_file else "",
                    context={"language": args.language, "purpose": args.purpose},
                    output_format=args.format, max_repairs=args.repairs, writing_mode=args.mode,
                )
            if args.command == "feedback":
                method = service.learn_from_feedback if args.learn else service.feedback
                return await method(
                    args.owner, args.run, against_version=args.against,
                    intent_version=args.intent_version,
                    edited_text=args.edit_file.read_text() if args.edit_file else None,
                    chosen_version=args.choose, explanation=args.explanation,
                    intent_changed=args.intent_changed, partition=args.partition,
                    event_id=args.event_id,
                )
            if args.command == "learn":
                return await service.learn(args.owner, args.event)
            if args.command == "validate":
                return await service.validate(args.owner, args.proposal)
            if args.command == "evaluate":
                return await service.evaluate(args.owner, args.policies)
            if args.command == "rollback":
                return service.rollback(args.owner, args.policy, output_format=args.format)
            if args.command == "evidence":
                return [service.add_evidence(args.owner, **row)
                        for row in json.loads(args.file.read_text())]
            return service.inspect(args.owner, output_format=args.format)

    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and result.get("status") in ("partial", "failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
