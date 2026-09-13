"""Deterministic controller tests; simulated judgments are not quality evidence."""

import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from mimicry.engine import rewrite
from mimicry.loop import LoopService
from mimicry.loop.learning import compile_proposal
from mimicry.loop.models import decision, digest, features, seed_policy

CONFIG = {"OPENAI_API_KEY": "secret-writer", "TYPESAFE_API_KEY": "secret-judge",
          "STYLE_WRITER_MODEL": "test-writer", "STYLE_JUDGE_MODEL": "test-judge-v1"}
CONTEXT = {"language": "en", "purpose": "build_update"}
REFS = "I shipped the parser. Found two bugs. Fixed one; the other gets tomorrow. " * 2
PROPOSAL = {"operation": "add", "name": "unrequested_lesson",
            "preference": "Avoid adding an unrequested generalized lesson.",
            "when": "Writing a concrete build update.",
            "exception": "Allow a lesson when the current source asks to teach one.",
            "exception_request": "I want to teach a lesson: measure before guessing.",
            "exception_candidate": "measure before guessing."}
SOURCES = ["The new parser handles unicode. I will release it tomorrow.",
           "Our warehouse lost power overnight; shipping will resume on Tuesday.",
           "I moved the checkout button after three customer interviews."]


def response_text(text):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}]}]}


class SimulatedAPI:
    def __init__(self):
        self.requests, self.writer_outputs = [], []
        self.fail_at = None
        self.attribution = 0.99
        self.guard = 0.99
        self.exception_applies = 0.01

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append((request.url.host, body))
        if len(self.requests) == self.fail_at:
            return httpx.Response(503)
        if request.url.host == "api.openai.com":
            if "text" in body:
                return httpx.Response(200, json=response_text(json.dumps(PROPOSAL)))
            state = json.loads(body["input"])
            text = (self.writer_outputs.pop(0) if self.writer_outputs else
                    state["source_text"] + ("" if state.get("feedback") else
                                             " Reminder: progress takes patience."))
            return httpx.Response(200, json=response_text(text))
        state = body["state"]
        candidate = state.get("candidate", "")
        answers = {}
        for key in body["questions"]:
            if key in ("same_intent", "style_evidence"):
                value = self.attribution
            elif key.endswith("_applies"):
                value = (self.exception_applies if "teach a lesson" in state["source_text"]
                         else 0.99)
            elif key.endswith("_violated"):
                value = 0.99 if "Reminder:" in candidate else 0.01
            elif "BAD_FACT" in candidate:
                value = 0.01
            elif key == "current_request_followed" and state["source_text"].startswith("Start"):
                value = 0.99 if candidate.startswith("we shipped") else 0.01
            else:
                value = self.guard
            answers[key] = {"type": "noul", "noul": value}
        return httpx.Response(200, json={"model": body["model"], "answers": answers})


def service_at(tmp_path, api):
    client = httpx.AsyncClient(transport=httpx.MockTransport(api))
    return LoopService(db_path=tmp_path / "loop.sqlite3", config=CONFIG, client=client)


async def correction(service, source, **kwargs):
    run = await service.rewrite("alice", source, references=REFS, context=CONTEXT, max_repairs=0)
    event = await service.feedback(
        "alice", run["id"], against_version="draft", edited_text=source,
        intent_version=run["intent_version"], explanation="Please remove the generic lesson.",
        **kwargs,
    )
    return run, event


def test_cache_invalidation_and_owner_isolation(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            provider = service.provider("alice", tmp_path)
            q = {"a": {"type": "noul", "instructions": "Does the candidate add a lesson?",
                        "criteria": {"true": "A lesson is added", "false": "No lesson"}}}
            state = {"candidate": "Example text"}
            await provider.measure(state, q, CONFIG["STYLE_JUDGE_MODEL"], "first")
            await provider.measure(state, q, CONFIG["STYLE_JUDGE_MODEL"], "cached")
            assert len(api.requests) == 1
            q["a"]["instructions"] += " Exclude intended lessons."
            await provider.measure(state, q, CONFIG["STYLE_JUDGE_MODEL"], "changed")
            assert len(api.requests) == 2
            other = service.provider("bob", tmp_path)
            await other.measure(state, q, CONFIG["STYLE_JUDGE_MODEL"], "other_owner")
            assert len(api.requests) == 3
            with pytest.raises(ValueError):
                service.store.get("bob", "policy", "alice-policy")
        await service.client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("value", [float("nan"), True, -0.1, 1.1, "0.5"])
def test_invalid_probabilities_are_rejected(value):
    with pytest.raises(ValueError):
        features({"q": {"type": "noul", "noul": value}}, {"q": {"type": "noul"}})


def test_one_draft_one_batch_no_score_chasing(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            result = await service.rewrite("alice", SOURCES[0], references=REFS)
            assert result["selected"] == "draft" and not result["review_required"]
            assert len(api.requests) == 2
            assert set(result["versions"]) == {"original", "draft"}
            assert "score" not in result["versions"]["draft"]
            assert "weights" not in result["evaluator"]
            assert all(q["type"] == "noul" for q in result["criteria"].values())
            assert result["budgets"] == {"drafts": 1, "max_repairs": 1}
        await service.client.aclose()
    asyncio.run(run())


def test_explicit_ordering_failure_triggers_exactly_one_repair(tmp_path):
    async def run():
        api = SimulatedAPI()
        wrong = "old plugins need an update. we shipped version 2."
        right = "we shipped version 2. old plugins need an update."
        api.writer_outputs = [wrong, right]
        async with service_at(tmp_path, api) as service:
            result = await service.rewrite("alice", "Start by announcing we shipped version 2. "
                                          "Then note that old plugins need an update.",
                                          references=REFS)
            assert result["selected"] == "repair"
            assert result["versions"]["draft"]["checks"]["failed"] == ["current_request_followed"]
            assert not result["review_required"] and len(api.requests) == 4
            payload = json.loads(api.requests[2][1]["input"])
            assert payload["feedback"]["failures"][0]["id"] == "current_request_followed"
        await service.client.aclose()
    asyncio.run(run())


def test_uncertainty_requires_review_without_retry(tmp_path):
    async def run():
        api = SimulatedAPI()
        api.guard = 0.5
        async with service_at(tmp_path, api) as service:
            result = await service.rewrite("alice", SOURCES[0], references=REFS)
            assert result["review_required"] and result["selected"] == "original"
            assert result["review_version"] == "draft"
            assert len(api.requests) == 2
            assert not result["versions"]["draft"]["checks"]["failed"]
        await service.client.aclose()
    asyncio.run(run())


def test_rule_applicability_is_separate_and_hard_failure_cannot_be_compensated():
    policy = compile_proposal(seed_policy("test-v1", "tweet"), PROPOSAL, CONTEXT, "edit")
    rule = policy["rules"][0]["id"]
    f = {k: 0.99 for k in policy["questions"]}
    f.update({rule + "_applies": 0.01, rule + "_violated": 0.99})
    assert decision({"features": f}, policy, CONTEXT)["passed"]
    f["current_request_followed"] = 0.01
    assert not decision({"features": f}, policy, CONTEXT)["passed"]
    f["current_request_followed"], f[rule + "_applies"] = 0.99, 0.99
    assert decision({"features": f}, policy, CONTEXT)["failed"] == [rule]


def test_two_edits_enable_rule_and_next_run_repairs_repeated_mistake(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            original, event = await correction(service, SOURCES[0])
            p = await service.learn("alice", event["id"])
            assert p["status"] == "shadow", p
            assert not (await service.validate("alice", p["id"]))["promoted"]
            _, second = await correction(service, SOURCES[1])
            report = await service.validate("alice", p["id"])
            assert report["promoted"], report
            result = await service.rewrite("alice", SOURCES[2], references=REFS, context=CONTEXT)
            assert result["selected"] == "repair" and not result["review_required"]
            assert "Reminder:" not in result["versions"]["repair"]["text"]
            assert len(result["memory_ids"]) == 1
            service.rollback("alice", original["evaluator_id"])
            assert not service.inspect("alice")["active_policy"]["rules"]
        await service.client.aclose()
    asyncio.run(run())


def test_synthetic_sealed_near_duplicate_and_bad_exception_cannot_enable_rule(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            _, event = await correction(service, SOURCES[0])
            proposal = await service.learn("alice", event["id"])
            await correction(service, SOURCES[0] + " ")
            _, synthetic = await correction(service, SOURCES[1], provenance="synthetic")
            _, sealed = await correction(service, SOURCES[2], partition="sealed")
            for e in (synthetic, sealed):
                with pytest.raises(ValueError, match="human development"):
                    await service.learn("alice", e["id"])
            assert not (await service.validate("alice", proposal["id"]))["promoted"]
        await service.client.aclose()
        api = SimulatedAPI()
        api.exception_applies = 0.99
        async with service_at(tmp_path / "bad", api) as service:
            _, event = await correction(service, SOURCES[0])
            assert (await service.learn("alice", event["id"]))["status"] == "rejected"
        await service.client.aclose()
    asyncio.run(run())


def test_bad_repair_keeps_safe_draft_for_review_and_policy_is_frozen(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            base = service.inspect("alice")["active_policy"]
            policy = compile_proposal(base, PROPOSAL, CONTEXT, "test")
            service.store.activate("alice", base["id"], policy, {"kind": "test"})
            api.writer_outputs = [SOURCES[0] + " Reminder: progress takes patience.", "BAD_FACT"]
            snapshots = []

            def snapshot(r):
                snapshots.append(r)
                if len(snapshots) == 2:
                    service.store.activate("alice", policy["id"], base, {"kind": "test"})
            result = await service.rewrite("alice", SOURCES[0], references=REFS, context=CONTEXT,
                                          on_snapshot=snapshot)
            assert result["selected"] == "draft" and result["review_required"]
            assert all(r["evaluator_id"] == policy["id"] for r in snapshots)
            assert all(r["evaluator_id"] == policy["id"] for r in result["steps"])
        await service.client.aclose()
    asyncio.run(run())


def test_feedback_idempotency_and_current_intent_boundary(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            result, _ = await correction(service, SOURCES[0])
            kwargs = dict(against_version="draft", edited_text=SOURCES[0],
                          intent_version=result["intent_version"], event_id="retry-key")
            a = await service.learn_from_feedback("alice", result["id"], **kwargs)
            count = len(api.requests)
            b = await service.learn_from_feedback("alice", result["id"], **kwargs)
            assert a == b and len(api.requests) == count
            with pytest.raises(ValueError, match="outdated"):
                await service.feedback("alice", result["id"],
                                       **(kwargs | {"intent_version": "old"}))
            with pytest.raises(ValueError, match="idempotency"):
                await service.feedback("alice", result["id"],
                                       **(kwargs | {"explanation": "changed"}))
            with pytest.raises(ValueError, match="belongs"):
                await service.feedback("bob", result["id"], **kwargs)
            changed = await service.feedback("alice", result["id"], **(kwargs | {
                "event_id": "changed-facts", "edited_text": "We will ship next year instead.",
                "intent_changed": True}))
            assert not changed["style_eligible"]
        await service.client.aclose()
    asyncio.run(run())


def test_later_human_edit_can_suspend_rule_and_sealed_edit_cannot(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            base = service.inspect("alice")["active_policy"]
            policy = compile_proposal(base, PROPOSAL, CONTEXT, "test")
            service.store.activate("alice", base["id"], policy, {"kind": "test"})
            for partition, source in (("sealed", SOURCES[0]), ("development", SOURCES[1])):
                api.writer_outputs = [source]
                result = await service.rewrite("alice", source, references=REFS, context=CONTEXT)
                outcome = await service.learn_from_feedback(
                    "alice", result["id"], against_version="draft",
                    edited_text=source + " Reminder: progress takes patience.",
                    intent_version=result["intent_version"], partition=partition)
                assert bool(outcome["suspended"]) == (partition == "development")
            assert not service.inspect("alice")["active_policy"]["rules"]
        await service.client.aclose()
    asyncio.run(run())


def test_provider_failure_and_cancellation_preserve_unverified_drafts(tmp_path):
    async def run():
        api = SimulatedAPI()
        api.fail_at = 2
        async with service_at(tmp_path, api) as service:
            result = await service.rewrite("alice", SOURCES[0], references=REFS)
            assert result["status"] == "partial" and result["review_required"]
            assert "draft" in result["versions"] and result["selected"] == "original"
            assert "secret-" not in json.dumps(result)
        await service.client.aclose()

        def cancelled(_):
            raise asyncio.CancelledError
        async with service_at(tmp_path / "cancel", cancelled) as service:
            with pytest.raises(asyncio.CancelledError):
                await service.rewrite("alice", SOURCES[0], references=REFS)
            assert service.store.list("alice", "run")[0]["status"] == "cancelled"
        await service.client.aclose()
    asyncio.run(run())


def test_duplicate_repair_stops_without_second_judgment(tmp_path):
    async def run():
        api = SimulatedAPI()
        api.writer_outputs = ["BAD_FACT", "BAD_FACT"]
        async with service_at(tmp_path, api) as service:
            result = await service.rewrite("alice", SOURCES[0], references=REFS)
            assert result["stop_reason"] == "duplicate" and result["review_required"]
            assert len(api.requests) == 3
        await service.client.aclose()
    asyncio.run(run())


def test_existing_engine_uses_bounded_loop_and_migrates_old_policy(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            base = service.inspect("alice")["active_policy"]
            legacy = deepcopy(base)
            legacy.update(id="legacy", version=1)
            service.store.activate("alice", base["id"], legacy, {"kind": "test"})
            assert service.inspect("alice")["active_policy"]["version"] == 2
            assert service.store.get("alice", "policy", "legacy")["version"] == 1
            result = await rewrite(REFS, SOURCES[0], owner_id="alice", context=CONTEXT,
                                   loop_store_path=service.store.path, config=CONFIG,
                                   client=service.client)
            assert result["status"] == "complete", result.get("error")
            assert result["budgets"]["max_repairs"] == 1
            assert result["evaluator_id"] == digest({k: v for k, v in result["evaluator"].items()
                                                      if k != "id"})[:24]
        await service.client.aclose()
    asyncio.run(run())


def test_sealed_evaluation_is_one_time_and_does_not_activate(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            original, event = await correction(service, SOURCES[0])
            proposal = await service.learn("alice", event["id"])
            await correction(service, SOURCES[1], partition="sealed")
            policies = [original["evaluator_id"], proposal["candidate_policy_id"]]
            report = await service.evaluate("alice", policies)
            assert report["status"] == "complete"
            assert report["results"][policies[0]]["both_passed"] == 1
            assert report["results"][policies[1]]["preferred_only_passed"] == 1
            assert not service.inspect("alice")["active_policy"]["rules"]
            with pytest.raises(ValueError, match="unused"):
                await service.evaluate("alice", policies)
        await service.client.aclose()
    asyncio.run(run())


def test_stale_shadow_cannot_overwrite_current_policy(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            _, event = await correction(service, SOURCES[0])
            proposal = await service.learn("alice", event["id"])
            await correction(service, SOURCES[1])
            base = service.inspect("alice")["active_policy"]
            new = seed_policy(CONFIG["STYLE_JUDGE_MODEL"], "tweet")
            service.store.activate("alice", base["id"], new, {"kind": "test"})
            with pytest.raises(ValueError, match="stale"):
                await service.validate("alice", proposal["id"])
            assert service.inspect("alice")["active_policy"]["id"] == new["id"]
        await service.client.aclose()
    asyncio.run(run())


def test_owner_paths_and_trace_export_do_not_expose_text(tmp_path):
    from mimicry.loop.store import LoopStore
    from mimicry.loop.tracking import WeaveSink

    store = LoopStore(tmp_path / "loop.sqlite3")
    assert store.output("alice", "../../outside").is_relative_to(tmp_path / "traces")

    class TraceClient:
        def create_call(self, **kwargs):
            self.inputs = kwargs
            return "call"

        def finish_call(self, call, output):
            self.output = output

    client = TraceClient()
    WeaveSink("test", client=client)({"action": "CHECK", "trace_id": "trace",
        "text": "private draft", "passed": False, "failed": ["current_request_followed"]})
    assert "private draft" not in json.dumps(client.inputs)
    assert client.output == {"action": "CHECK", "passed": False,
                             "failed": ["current_request_followed"]}


@pytest.mark.parametrize("mode,label", [
    ("refine_personalize", "AI refine + Personalize"),
    ("personalize", "Personalize only"),
    ("refine", "AI refine only"),
])
def test_three_modes_control_writer_context_and_judge_rules(tmp_path, mode, label):
    async def run():
        api = SimulatedAPI()
        async with service_at(tmp_path, api) as service:
            base = service.inspect("alice")["active_policy"]
            policy = compile_proposal(base, PROPOSAL, CONTEXT, "test")
            service.store.activate("alice", base["id"], policy, {"kind": "test"})
            service.add_evidence("alice", REFS, context=CONTEXT)
            result = await service.rewrite("alice", SOURCES[0], context=CONTEXT,
                                          writing_mode=mode, max_repairs=0)
            assert result["status"] == "complete"
            assert result["writing_mode"] == mode
            assert label in api.requests[0][1]["instructions"]
            personal = mode != "refine"
            assert bool(result["memory_ids"]) == personal
            assert len(result["criteria"]) == (5 if personal else 3)
            writer_state = json.loads(api.requests[0][1]["input"])
            assert bool(writer_state.get("references")) == personal
            assert bool(writer_state.get("preferences")) == personal
            if not personal:
                assert not result["evidence_ids"] and not result["references"]
                assert "Reminder:" in result["versions"]["draft"]["text"]
                assert not result["review_required"]
            if mode == "personalize":
                assert "Do not independently polish" in api.requests[0][1]["instructions"]
        await service.client.aclose()
    asyncio.run(run())


def test_refine_needs_no_examples_does_not_teach_style_and_mode_survives_repair(tmp_path):
    async def run():
        api = SimulatedAPI()
        api.writer_outputs = ["BAD_FACT", SOURCES[0]]
        async with service_at(tmp_path, api) as service:
            result = await service.rewrite("alice", SOURCES[0], writing_mode="refine")
            assert result["selected"] == "repair"
            writes = [b for host, b in api.requests if host == "api.openai.com"]
            assert len(writes) == 2
            assert all("Mode: AI refine only." in b["instructions"] for b in writes)
            assert all("references" not in json.loads(b["input"]) for b in writes)
            count = len(api.requests)
            edited = await service.learn_from_feedback(
                "alice", result["id"], against_version="repair",
                edited_text="the parser handles unicode. releasing it tomorrow.",
                intent_version=result["intent_version"])
            assert edited["feedback"]["attribution_status"] == "refine_only"
            assert not edited["feedback"]["style_eligible"] and not edited["proposal"]
            assert len(api.requests) == count
            for mode in ("personalize", "refine_personalize", "unknown", None):
                with pytest.raises(ValueError):
                    await service.rewrite("alice", SOURCES[0], writing_mode=mode)
        await service.client.aclose()
    asyncio.run(run())


def test_engine_passes_mode_to_loop(tmp_path):
    async def run():
        api = SimulatedAPI()
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
            result = await rewrite("", SOURCES[0], owner_id="alice", writing_mode="refine",
                                   loop_store_path=tmp_path / "loop.sqlite3", config=CONFIG,
                                   client=client)
            assert result["writing_mode"] == "refine" and result["status"] == "complete"
    asyncio.run(run())
