"""Verify immutable source meaning, feedback, rollback, and partial recovery."""

import asyncio
import json

import httpx
import pytest

from mimicry.engine import choose_version, parse_text, rewrite, summarize_judgment
from mimicry.examples import DRAFT, PRESETS


@pytest.mark.parametrize(
    "mode,expected_hosts",
    [
        ("refine_personalize", ["openai", "typesafe", "typesafe", "openai", "typesafe"]),
        ("personalize", ["typesafe", "openai", "typesafe"]),
        ("refine", ["openai", "typesafe"]),
    ],
)
def test_writing_modes_execute_only_the_requested_stages(tmp_path, mode, expected_hosts):
    calls = []
    source = "The coffee machine is broken. Repair takes three days."

    def handler(request):
        body = json.loads(request.content)
        provider = "typesafe" if request.url.host == "api.typesafe.ai" else "openai"
        calls.append((provider, body))
        if provider == "typesafe":
            number = sum(p == "typesafe" for p, _ in calls)
            response = judgment(score=min(number + 1, 4), economy=min(number + 1, 4))
            response["answers"] = {
                k: v for k, v in response["answers"].items() if k in body["questions"]
            }
            return httpx.Response(200, json=response)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": f"Coffee machine repair: three days. {len(calls)}",
                            }
                        ],
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await rewrite(
                PRESETS["Dry humor"],
                source,
                writing_mode=mode,
                max_revisions=1,
                client=client,
                output_root=tmp_path,
                config={
                    "OPENAI_API_KEY": "mock-writer",
                    "TYPESAFE_API_KEY": "mock-judge",
                    "STYLE_WRITER_MODEL": "mock",
                    "STYLE_JUDGE_MODEL": "mock",
                },
            )

    result = asyncio.run(run())
    assert [provider for provider, _ in calls] == expected_hosts
    assert result["status"] == "complete" and result["writing_mode"] == mode
    assert ("improved" in result["versions"]) == (mode != "personalize")
    assert ("rewrite_1" in result["versions"]) == (mode != "refine")
    if mode == "refine":
        assert result["references"] == ""
        assert result["selected"] == "improved"
        assert result["versions"]["improved"]["judgment"]["style_mean"] is None
        assert set(calls[1][1]["questions"]) == {"facts_preserved", "no_invented_facts"}
        assert PRESETS["Dry humor"] not in json.dumps(calls)
    if mode == "personalize":
        state = json.loads(calls[1][1]["input"])
        assert state["current_text"] == source
        assert state["references"] == PRESETS["Dry humor"]


@pytest.mark.parametrize("facts,text", [(0.4, "A changed statement."), (0.99, "x" * 281)])
def test_refine_needs_no_samples_but_rejects_failed_meaning_or_tweet_length(tmp_path, facts, text):
    def handler(request):
        if request.url.host == "api.typesafe.ai":
            return httpx.Response(
                200,
                json={
                    "answers": {
                        key: {"type": "noul", "noul": facts}
                        for key in ("facts_preserved", "no_invented_facts")
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await rewrite(
                "",
                "My original statement.",
                writing_mode="refine",
                output_format="tweet",
                client=client,
                output_root=tmp_path,
                config={
                    "OPENAI_API_KEY": "mock-writer",
                    "TYPESAFE_API_KEY": "mock-judge",
                    "STYLE_WRITER_MODEL": "mock",
                    "STYLE_JUDGE_MODEL": "mock",
                },
            )

    result = asyncio.run(run())
    assert result["status"] == "complete"
    assert result["selected"] == "original"
    assert not result["steps"][0]["accepted"]


def judgment(score=2, facts=0.95, economy=2):
    return {
        "answers": {
            **{
                k: {"type": "score", "score": score}
                for k in ("voice", "rhythm", "rhetoric", "structure")
            },
            "economy": {"type": "score", "score": economy},
            **{
                k: {"type": "noul", "noul": facts} for k in ("facts_preserved", "no_invented_facts")
            },
        }
    }


@pytest.mark.parametrize(
    "score,facts,economy,accepted",
    [
        (4, 0.2, 4, False),  # Style cannot compensate for changed meaning.
        (1, 0.95, 4, True),  # Voice ratings do not veto a reviewable suggestion.
        (4, 0.95, 1, True),  # Filler ratings are advisory.
        (2, 0.95, 2, True),  # Equal ratings need not mean identical wording.
        (2, 0.95, 3, True),  # Removing filler alone can be useful.
        (3, 0.95, 2, True),
    ],
)
def test_suggestion_requires_meaning_but_style_is_advisory(score, facts, economy, accepted):
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


@pytest.mark.parametrize(
    "references,source,expected",
    [
        ("", DRAFT, "Add at least 80 more"),
        (" \n ", DRAFT, "Add at least 80 more"),
        ("x" * 79, DRAFT, "Add at least 1 more"),
        ("x" * 12001, DRAFT, "Remove at least 1"),
        (PRESETS["Dry humor"], "x" * 9, "AI draft: 9 characters"),
    ],
)
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


@pytest.mark.parametrize(
    "fail_call,selected,expected_calls",
    [
        (None, "rewrite_1", 5),  # Stop at the first meaning-checked personal suggestion.
        (1, "original", 1),  # Initial clarity improvement fails; preserve the input.
        (2, "original", 2),  # Original judgment fails; retain unjudged improvement.
        (3, "original", 3),  # Ungraded output is saved but never auto-selected.
        (4, "improved", 4),
        (5, "improved", 5),
    ],
)
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
            score = 3 if len(calls) == 5 else 2
            return httpx.Response(200, json=judgment(score=score, economy=score))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": f"Rewrite from call {len(calls)}."}
                        ],
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await rewrite(
                PRESETS["Dry humor"],
                source,
                output_root=tmp_path,
                client=client,
                output_format=output_format,
                config={
                    "OPENAI_API_KEY": "secret-writer",
                    "TYPESAFE_API_KEY": "secret-judge",
                    "STYLE_WRITER_MODEL": "test-writer",
                    "STYLE_JUDGE_MODEL": "test-judge",
                },
            )

    result = asyncio.run(run())
    assert len(calls) == expected_calls
    assert calls[0][0] == "api.openai.com"
    assert "references" not in json.loads(calls[0][1]["input"])
    if len(calls) >= 2:
        assert calls[1][1]["state"]["candidate"] == source
    for i, (host, body) in enumerate(calls):
        state = body["state"] if host == "api.typesafe.ai" else json.loads(body["input"])
        assert state["source_text"] == source
        assert state["output_format"] == output_format
        if i:
            assert state["references"] == PRESETS["Dry humor"]
    if len(calls) >= 4:
        first_input = json.loads(calls[3][1]["input"])
        assert first_input["current_text"] == "Rewrite from call 1."
        assert first_input["feedback"]["ratings"]["style_mean"] == 2
        assert first_input["feedback"]["rejected_attempts"] == []
    if len(calls) >= 6:
        second_input = json.loads(calls[5][1]["input"])
        assert second_input["current_text"] == "Rewrite from call 4."
        assert second_input["feedback"]["ratings"]["style_mean"] == 3
    assert result["versions"]["original"]["text"] == source
    assert result["selected"] == selected
    assert result["status"] == ("partial" if fail_call else "complete")
    saved = list(tmp_path.glob("*/result.json"))[0].read_text()
    assert "secret-writer" not in saved and "secret-judge" not in saved
    assert list(tmp_path.glob("*/writing.txt"))[0].read_text() == (
        source if selected == "original" else (
            "Rewrite from call 1." if selected == "improved" else "Rewrite from call 4."
        )
    )
    if not fail_call:
        assert [step["accepted"] for step in result["steps"]] == [True, True]
