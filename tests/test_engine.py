"""Verify immutable source meaning, feedback, rollback, and partial recovery."""

import asyncio
import json

import httpx
import pytest

from mimicry.engine import choose_version, parse_text, rewrite, summarize_judgment
from mimicry.examples import DRAFT, PRESETS


def judgment(score=2, facts=0.95, economy=2):
    return {"answers": {
        **{k: {"type": "score", "score": score}
           for k in ("voice", "rhythm", "rhetoric", "structure")},
        "economy": {"type": "score", "score": economy},
        **{k: {"type": "noul", "noul": facts}
           for k in ("facts_preserved", "no_invented_facts")},
    }}


@pytest.mark.parametrize("score,facts,economy,accepted", [
    (4, 0.2, 4, False),  # Style cannot compensate for changed meaning.
    (1, 0.95, 4, False),  # Less filler cannot compensate for lost voice.
    (4, 0.95, 1, False),  # More style cannot compensate for added filler.
    (2, 0.95, 2, False),  # No change is not an improvement.
    (2, 0.95, 3, True),  # Removing filler alone can be useful.
    (3, 0.95, 2, True),
])
def test_acceptance_requires_meaning_and_no_regression(score, facts, economy, accepted):
    before = {"judgment": summarize_judgment(judgment())}
    after = {"judgment": summarize_judgment(judgment(score, facts, economy))}
    assert choose_version(before, after)[0] is accepted


@pytest.mark.parametrize("score", [float("nan"), 5, True, "3"])
def test_invalid_provider_scores_are_rejected(score):
    with pytest.raises(ValueError, match="invalid answer"):
        summarize_judgment(judgment(score=score))


def test_truncated_response_is_not_accepted():
    with pytest.raises(ValueError, match="did not finish"):
        parse_text({"status": "incomplete", "output": []})


@pytest.mark.parametrize("references,source,expected", [
    ("", DRAFT, "Add at least 80 more"),
    (" \n ", DRAFT, "Add at least 80 more"),
    ("x" * 79, DRAFT, "Add at least 1 more"),
    ("x" * 12001, DRAFT, "Remove at least 1"),
    (PRESETS["Dry humor"], "x" * 9, "AI draft: 9 characters"),
])
def test_invalid_inputs_do_not_call_providers_or_create_runs(
    tmp_path, references, source, expected
):
    async def run():
        def handler(request):
            pytest.fail("Invalid input must never reach an external provider")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await rewrite(references, source, client=client, output_root=tmp_path, config={})

    with pytest.raises(ValueError, match=expected):
        asyncio.run(run())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fail_call,selected,expected_calls", [
    (None, "rewrite_1", 5),  # Second rewrite regresses and is rolled back.
    (1, "original", 1),  # Baseline judgment fails: still preserve the input.
    (2, "original", 2),  # Writer fails: no fabricated initial draft.
    (3, "original", 3),  # Ungraded output is saved but never auto-selected.
    (4, "rewrite_1", 4),  # Previously accepted rewrite survives a later failure.
    (5, "rewrite_1", 5),
])
@pytest.mark.parametrize("output_format", ["text", "tweet"])
def test_feedback_loop_and_partial_recovery(
    tmp_path, fail_call, selected, expected_calls, output_format
):
    calls = []
    source = "  " + DRAFT + chr(10)

    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.host, body))
        if len(calls) == fail_call:
            return httpx.Response(503)
        if request.url.host == "api.typesafe.ai":
            score = 3 if len(calls) == 3 else 2
            return httpx.Response(200, json=judgment(score=score, economy=score))
        return httpx.Response(200, json={
            "status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": f"Rewrite from call {len(calls)}."}
            ]}],
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await rewrite(
                PRESETS["Dry humor"], source, output_root=tmp_path, client=client,
                output_format=output_format,
                config={"OPENAI_API_KEY": "secret-writer", "TYPESAFE_API_KEY": "secret-judge",
                        "STYLE_WRITER_MODEL": "test-writer", "STYLE_JUDGE_MODEL": "test-judge"},
            )

    result = asyncio.run(run())
    assert len(calls) == expected_calls
    assert calls[0][0] == "api.typesafe.ai"
    assert calls[0][1]["state"]["candidate"] == source
    for host, body in calls:
        state = body["state"] if host == "api.typesafe.ai" else json.loads(body["input"])
        assert state["source_text"] == source
        assert state["output_format"] == output_format
        assert state["references"] == PRESETS["Dry humor"]
    if len(calls) >= 2:
        first_input = json.loads(calls[1][1]["input"])
        assert first_input["current_text"] == source
        assert first_input["feedback"]["ratings"]["style_mean"] == 2
    if len(calls) >= 4:
        second_input = json.loads(calls[3][1]["input"])
        assert second_input["current_text"] == "Rewrite from call 2."
        assert second_input["feedback"]["ratings"]["style_mean"] == 3
    assert result["versions"]["original"]["text"] == source
    assert result["selected"] == selected
    assert result["status"] == ("partial" if fail_call else "complete")
    saved = list(tmp_path.glob("*/result.json"))[0].read_text()
    assert "secret-writer" not in saved and "secret-judge" not in saved
    assert list(tmp_path.glob("*/writing.txt"))[0].read_text() == (
        source if selected == "original" else "Rewrite from call 2."
    )
    if not fail_call:
        assert [step["accepted"] for step in result["steps"]] == [True, False]
