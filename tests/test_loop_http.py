"""Exercise the Chrome HTTP boundary with real storage and simulated providers."""

import asyncio
import json
import time

import httpx
import pytest
from starlette.testclient import TestClient
from test_personal_loop import CONFIG, CONTEXT, REFS, SOURCES, SimulatedAPI

from mimicry.extension_identity import EXTENSION_ORIGIN
from mimicry.server import create_app


@pytest.fixture
def browser(tmp_path):
    provider = SimulatedAPI()
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app = create_app(CONFIG, client=upstream, output_root=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:2719") as client:
        session = client.get("/api/session").json()
        client.headers.update({"Origin": EXTENSION_ORIGIN, "x-csrf-token": session["csrf"]})
        yield client, provider, app
    asyncio.run(upstream.aclose())


def start(client, request_id="write-1", **overrides):
    return client.post("/api/loop/rewrite", json={"request_id": request_id,
        "source_text": SOURCES[0], "references": REFS, "context": CONTEXT, **overrides})


def finished(client, id_):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get("/api/loop/jobs/" + id_)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] != "running":
            return job
        time.sleep(0.01)
    pytest.fail("Background loop job did not complete.")


def test_modes_progress_results_and_idempotent_start(browser):
    client, provider, _ = browser
    profile = client.get("/api/loop/profile").json()
    assert profile["contract_version"] == 1
    assert set(profile["modes"]) == {"refine_personalize", "personalize", "refine"}
    response = start(client)
    assert response.status_code == 202, response.text
    job = finished(client, response.json()["id"])
    assert job["status"] == "complete" and job["revision"] > 1
    result = job["result"]
    assert result["selected"] == "draft" and not result["review_required"]
    assert "current_request_followed" in result["criteria"]
    assert "evaluator" not in result
    serialized = json.dumps(job)
    for hidden in ("secret-writer", "secret-judge", "output_dir", REFS, "fingerprint"):
        assert hidden not in serialized
    count = len(provider.requests)
    retried = start(client)
    assert retried.json()["id"] == job["id"] and len(provider.requests) == count
    assert start(client, source_text=SOURCES[1]).status_code == 409
    refine = start(client, "refine-1", writing_mode="refine", references="")
    refined = finished(client, refine.json()["id"])
    assert refined["status"] == "complete" and len(refined["result"]["criteria"]) == 3


def test_feedback_and_profile_are_usable_without_raw_learning_records(browser):
    client, _, _ = browser
    job = finished(client, start(client).json()["id"])
    run = job["result"]
    payload = {"request_id": "edit-1", "run_id": run["run_id"], "against_version": "draft",
               "intent_version": run["intent_version"], "edited_text": SOURCES[0],
               "explanation": "Please remove the generic lesson."}
    response = client.post("/api/loop/feedback", json=payload)
    assert response.status_code == 202, response.text
    feedback = finished(client, response.json()["id"])
    assert feedback["status"] == "complete", feedback
    assert feedback["result"]["proposal"]["status"] == "shadow"
    assert "self_check" not in json.dumps(feedback)
    assert client.post("/api/loop/feedback", json=payload).json()["id"] == feedback["id"]
    profile = client.get("/api/loop/profile").json()
    assert profile["proposals"][0]["status"] == "shadow"
    stale = payload | {"request_id": "edit-stale", "intent_version": "stale"}
    assert client.post("/api/loop/feedback", json=stale).status_code == 409
    forged = payload | {"request_id": "edit-forged", "provenance": "human"}
    assert client.post("/api/loop/feedback", json=forged).status_code == 400


def test_origin_csrf_and_session_isolation(browser):
    client, _, _ = browser
    job = finished(client, start(client).json()["id"])
    for headers in ({"Origin": "https://evil.example"}, {"x-csrf-token": "wrong"}):
        assert client.get("/api/loop/profile", headers=headers).status_code == 403
        assert client.get("/api/loop/jobs/" + job["id"], headers=headers).status_code == 403
    client.cookies.clear()
    assert client.get("/api/loop/profile").status_code == 401
    session = client.get("/api/session").json()
    client.headers["x-csrf-token"] = session["csrf"]
    assert client.get("/api/loop/jobs/" + job["id"]).status_code == 404
    run = job["result"]
    response = client.post("/api/loop/feedback", json={"request_id": "other-owner",
        "run_id": run["run_id"], "against_version": "draft",
        "intent_version": run["intent_version"], "edited_text": SOURCES[0]})
    assert response.status_code == 404
    assert start(client, owner_id="victim").status_code == 400


@pytest.mark.parametrize("fields", [
    {"request_id": ""}, {"source_text": "short"}, {"context": {"language": "en"}},
    {"writing_mode": []}, {"output_format": "thread"}, {"references": "short"},
    {"model": "another-model"}, {"source_text": 123},
])
def test_bad_inputs_fail_before_provider_calls(browser, fields):
    client, provider, _ = browser
    assert start(client, **fields).status_code == 400
    assert not provider.requests


def test_cancel_background_job_and_prevent_concurrent_generation(tmp_path):
    async def slow(request):
        await asyncio.sleep(30)
        return httpx.Response(500)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    with TestClient(create_app(CONFIG, client=upstream, output_root=tmp_path),
                    base_url="http://127.0.0.1:2719") as client:
        session = client.get("/api/session").json()
        client.headers.update({"Origin": EXTENSION_ORIGIN, "x-csrf-token": session["csrf"]})
        job = start(client).json()
        assert start(client, "another").status_code == 409
        stopped = client.post("/api/loop/jobs/" + job["id"] + "/cancel")
        assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
        assert client.post("/api/loop/jobs/" + job["id"] + "/cancel").json() == stopped.json()
    asyncio.run(upstream.aclose())


def test_verified_x_identity_persists_memory_but_jobs_remain_session_scoped(browser):
    client, _, app = browser
    session = next(iter(app.state.sessions.values()))
    session.update(user={"id": "verified-x-id"}, auth={"expires_at": time.time() + 3600})
    policy = client.get("/api/loop/profile").json()["policy_id"]
    job = finished(client, start(client).json()["id"])
    resumed = client.get("/api/loop/profile").json()["jobs"]
    assert resumed[0]["id"] == job["id"] and resumed[0]["request_id"] == "write-1"
    client.cookies.clear()
    new = client.get("/api/session").json()
    client.headers["x-csrf-token"] = new["csrf"]
    session = next(s for s in app.state.sessions.values() if s["csrf"] == new["csrf"])
    session.update(user={"id": "verified-x-id"}, auth={"expires_at": time.time() + 3600})
    assert client.get("/api/loop/profile").json()["policy_id"] == policy
    assert client.get("/api/loop/jobs/" + job["id"]).status_code == 404
    run = job["result"]
    accepted = client.post("/api/loop/feedback", json={"request_id": "new-session-edit",
        "run_id": run["run_id"], "against_version": "draft",
        "intent_version": run["intent_version"], "edited_text": SOURCES[0]})
    assert accepted.status_code == 202
    assert finished(client, accepted.json()["id"])["status"] == "complete"


def test_full_post_format_accepts_long_faithful_output_and_selects_matching_profile(browser):
    client, provider, _ = browser
    source = "I tested the parser against the example files. " * 15
    provider.writer_outputs = [source + " Production traffic is still untested."]
    response = start(client, "long-post", source_text=source, output_format="text")
    job = finished(client, response.json()["id"])
    assert job["status"] == "complete" and job["result"]["selected_eligible"]
    assert len(job["result"]["versions"]["draft"]["text"]) > 280
    assert job["result"]["versions"]["draft"]["tweet_check"]["valid"]
    profile = client.get("/api/loop/profile?output_format=text").json()
    assert profile["output_format"] == "text"
    assert client.get("/api/loop/profile?output_format=bad").status_code == 400
