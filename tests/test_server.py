"""Exercise OAuth binding, isolation, failure handling, and the writing API without real keys."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from mimicry.examples import DRAFT, PRESETS
from mimicry.extension_identity import EXTENSION_ORIGIN
from mimicry.server import COOKIE, create_app

ORIGIN = "http://127.0.0.1:2719"
CONFIG = {
    "X_CLIENT_ID": "test-client", "X_CLIENT_SECRET": "test-secret",
    "X_REDIRECT_URI": ORIGIN + "/auth/x/callback",
    "OPENAI_API_KEY": "test-writer", "TYPESAFE_API_KEY": "test-judge",
    "STYLE_WRITER_MODEL": "mock", "STYLE_JUDGE_MODEL": "mock",
}


@pytest.fixture
def site(tmp_path):
    calls, failures = [], {}

    def provider(request):
        calls.append(request)
        path = request.url.path
        if path in failures:
            return httpx.Response(failures[path], text="test-secret test-access-token")
        if path == "/2/oauth2/token":
            return httpx.Response(200, json={
                "access_token": "test-access-token", "token_type": "bearer",
                "expires_in": 7200, "scope": "users.read tweet.read",
            })
        if path == "/2/users/me":
            return httpx.Response(200, json={"data": {
                "id": "123", "username": "test_author", "name": "Test Writer",
            }})
        if path == "/2/users/123/tweets":
            return httpx.Response(200, json={"data": [
                {"id": "100", "text": "My own standalone writing."},
                {"id": "101", "text": "Quoted content", "referenced_posts": [{"id": "99"}]},
            ]})
        if path == "/2/users/123/timelines/reverse_chronological":
            return httpx.Response(200, json={
                "data": [{"id": "200", "text": "Another writer's thought.", "author_id": "456"}],
                "includes": {"users": [{"id": "456", "username": "another_writer"}]},
            })
        if path == "/2/oauth2/revoke":
            return httpx.Response(200, json={"revoked": True})
        pytest.fail(f"Unexpected provider endpoint: {path}")

    async def fake_rewrite(references, source, **kwargs):
        result = {"source_text": source, "references": references, "status": "complete",
                  "writing_mode": kwargs.get("writing_mode", "refine_personalize"),
                  "versions": {"original": {"text": source}}, "selected": "original", "steps": [],
                  "feed_context": kwargs["feed_context"], "stop_reason": "Test finished."}
        kwargs["on_snapshot"](result)
        return result

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app = create_app(CONFIG, client=upstream, output_root=tmp_path, rewrite_fn=fake_rewrite)
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as browser:
        yield browser, app, calls, failures


def boot(browser):
    response = browser.get("/api/session")
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf"]}


def begin(browser, headers, **workspace):
    response = browser.post("/auth/x/start", headers=headers, json=workspace)
    assert response.status_code == 200
    return parse_qs(urlsplit(response.json()["url"]).query)


def sign_in(browser):
    headers = boot(browser)
    query = begin(browser, headers)
    response = browser.get("/auth/x/callback", params={
        "state": query["state"][0], "code": "test-code"})
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    return boot(browser)


def test_login_pkce_rotation_and_read_only_timelines(site):
    browser, app, calls, _ = site
    headers = boot(browser)
    old_sid = browser.cookies.get(COOKIE)
    query = begin(browser, headers, source_text="My unfinished draft")
    assert query["scope"] == ["tweet.read users.read"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [CONFIG["X_REDIRECT_URI"]]
    pending = app.state.sessions[old_sid]["pending"]
    expected = base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest())
    assert query["code_challenge"] == [expected.decode().rstrip("=")]
    response = browser.get("/auth/x/callback", params={
        "state": pending["state"], "code": "test-code"})
    assert response.status_code == 303
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert old_sid not in app.state.sessions
    assert browser.cookies.get(COOKIE) != old_sid
    assert calls[0].headers["authorization"] == "Basic " + base64.b64encode(
        b"test-client:test-secret").decode()
    form = parse_qs(calls[0].content.decode())
    assert form["code_verifier"] == [pending["verifier"]]
    assert form["grant_type"] == ["authorization_code"]
    assert calls[1].headers["authorization"] == "Bearer test-access-token"
    session = browser.get("/api/session")
    assert session.json()["user"]["username"] == "test_author"
    assert session.json()["workspace"]["source_text"] == "My unfinished draft"
    assert all(secret not in session.text for secret in (
        "test-secret", "test-access-token", pending["verifier"], "test-code"))
    headers = boot(browser)
    posts = browser.post("/api/x/posts", json={}, headers=headers).json()
    assert [p["id"] for p in posts["posts"]] == ["100"]
    feed = browser.post("/api/x/feed", json={}, headers=headers).json()
    assert feed["posts"][0]["username"] == "another_writer"
    assert feed["kind"] == "reverse_chronological"
    assert all(r.url.host == "api.x.com" for r in calls)
    assert browser.get("/auth/x/callback", params={
        "state": pending["state"], "code": "test-code"}).headers["location"] == "/?signin=invalid"
    assert len([r for r in calls if r.url.path == "/2/oauth2/token"]) == 1


@pytest.mark.parametrize("failure", [
    "missing", "wrong_state", "unicode_state", "expired", "cancelled", "no_code",
])
def test_invalid_callbacks_never_exchange_credentials(site, failure):
    browser, app, calls, _ = site
    query = begin(browser, boot(browser))
    params = {"state": query["state"][0], "code": "test-code"}
    if failure == "missing":
        browser.cookies.clear()
    elif failure == "wrong_state":
        params["state"] = "wrong"
    elif failure == "unicode_state":
        params["state"] = "invalid-\u2603"
    elif failure == "expired":
        app.state.sessions[browser.cookies.get(COOKIE)]["pending"]["expires_at"] = 0
    elif failure == "cancelled":
        params["error"] = "access_denied"
    else:
        params.pop("code")
    assert browser.get("/auth/x/callback", params=params).status_code == 303
    assert calls == []
    assert browser.get("/api/session").json()["user"] is None


def test_csrf_host_and_no_session_protection(site):
    browser, _, calls, _ = site
    assert browser.post("/auth/x/start", json={}).status_code == 401
    headers = boot(browser)
    for path in ("/auth/x/start", "/auth/x/logout", "/api/x/posts", "/api/rewrite"):
        assert browser.post(path, json={}).status_code == 403
        assert browser.post(path, json={}, headers={
            **headers, "Origin": "https://untrusted.example"}).status_code == 403
    assert browser.get("/api/session", headers={"Host": "untrusted.example"}).status_code == 400
    assert calls == []


def test_logout_clears_session_even_when_revocation_fails(site):
    browser, app, _, failures = site
    headers = sign_in(browser)
    sid = browser.cookies.get(COOKIE)
    failures["/2/oauth2/revoke"] = 503
    response = browser.post("/auth/x/logout", json={}, headers=headers)
    assert response.status_code == 200
    assert "could not confirm token revocation" in response.json()["message"]
    assert sid not in app.state.sessions
    assert browser.get("/api/session").json()["user"] is None
    assert browser.post("/api/x/posts", json={}, headers=boot(browser)).status_code == 401


def test_bundled_extension_origin_still_requires_session_and_csrf(site):
    browser, _, calls, _ = site
    headers = {"Origin": EXTENSION_ORIGIN}
    assert browser.post("/auth/x/start", json={}, headers=headers).status_code == 401
    headers["X-CSRF-Token"] = boot(browser)["X-CSRF-Token"]
    for origin in ("chrome-extension://another-extension", "null"):
        response = browser.post("/auth/x/start", json={}, headers={**headers, "Origin": origin})
        assert response.status_code == 403
    assert browser.post("/auth/x/start", json={}, headers={
        **headers, "X-CSRF-Token": "wrong",
    }).status_code == 403
    assert browser.post("/auth/x/start", json={}, headers=headers).status_code == 200
    assert calls == []


def test_provider_errors_are_safe_and_expiry_requires_login(site):
    browser, app, _, failures = site
    headers = sign_in(browser)
    failures["/2/users/123/tweets"] = 402
    response = browser.post("/api/x/posts", json={}, headers=headers)
    assert "credits" in response.json()["error"]
    assert "test-access-token" not in response.text and "test-secret" not in response.text
    auth = app.state.sessions[browser.cookies.get(COOKIE)]["auth"]
    auth["expires_at"] = time.time() - 1
    assert browser.post("/api/x/feed", json={}, headers=headers).status_code == 401
    assert browser.get("/api/session").json()["user"] is None


def test_token_exchange_failure_preserves_draft_and_hides_upstream_error(site):
    browser, _, _, failures = site
    query = begin(browser, boot(browser), source_text="A thought worth keeping.")
    failures["/2/oauth2/token"] = 401
    assert browser.get("/auth/x/callback", params={
        "state": query["state"][0], "code": "test-code"}).status_code == 303
    response = browser.get("/api/session")
    assert response.json()["workspace"]["source_text"] == "A thought worth keeping."
    assert "rejected" in response.json()["notice"]
    assert "test-secret" not in response.text and "test-access-token" not in response.text


def test_private_jobs_and_timeline_cache_are_session_scoped(site):
    browser, _, _, _ = site
    headers = sign_in(browser)
    browser.post("/api/x/posts", json={}, headers=headers)
    response = browser.post("/api/rewrite", headers=headers, json={
        "references": PRESETS["Dry humor"], "source_text": DRAFT,
        "feed_context": "Another writer's thought.",
    })
    assert response.status_code == 202
    job_id = response.json()["id"]
    job = browser.get("/api/jobs/" + job_id).json()
    assert job["result"]["source_text"] == DRAFT
    assert job["result"]["feed_context"] == "Another writer's thought."
    browser.cookies.clear()
    new_user = browser.get("/api/session").json()
    assert new_user["posts"] is None and new_user["user"] is None
    assert new_user["job_id"] is None and new_user["workspace"] == {}
    assert browser.get("/api/jobs/" + job_id).status_code == 404


def test_static_page_and_bare_callback_are_usable(site):
    browser, _, _, _ = site
    assert browser.get("/").status_code == 200
    response = browser.get("/assets/app.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert browser.get("/auth/x/callback").headers["location"] == "/?signin=invalid"
    assert browser.get("/assets/.env").status_code == 404
    assert browser.get("/inline-demo").status_code == 200
    assert browser.get("/inline-assets/composer.js").status_code == 200
    assert browser.get("/inline-assets/.env").status_code == 404


def test_refine_mode_accepts_empty_samples_and_is_forwarded_to_engine(site):
    browser, _, _, _ = site
    headers = boot(browser)
    response = browser.post("/api/rewrite", headers=headers, json={
        "source_text": "My rough opinion.", "writing_mode": "refine",
    })
    assert response.status_code == 202
    job = browser.get("/api/jobs/" + response.json()["id"]).json()
    assert job["result"]["writing_mode"] == "refine"
    assert browser.post("/api/rewrite", headers=headers, json={
        "source_text": "My rough opinion.", "writing_mode": "personalize",
    }).status_code == 400
    assert browser.post("/api/rewrite", headers=headers, json={
        "source_text": "My rough opinion.", "writing_mode": "not-a-mode",
    }).status_code == 400
