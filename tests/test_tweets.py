"""Test public writing imports and Unicode tweet length boundaries."""

import asyncio
import json

import httpx
import pytest

from mimicry.engine import choose_version, summarize_judgment
from mimicry.tweets import import_tweets, normalize_posts, tweet_check


@pytest.mark.parametrize("text,length,valid", [
    ("a" * 280, 280, True),
    ("a" * 281, 281, False),
    ("\u4e2d" * 140, 280, True),
    ("\u4e2d" * 141, 282, False),
    ("👨‍👩‍👧‍👦", 2, True),
    ("cafe\u0301", 4, True),
    ("https://example.com/very/long/path", 23, True),
])
def test_weighted_length(text, length, valid):
    assert tweet_check(text) == {"weighted_length": length, "limit": 280, "valid": valid}


def test_normalization_uses_full_own_text_and_excludes_context_dependent_posts():
    posts = [
        {"id": "1", "text": "Short preview", "note_post": {"text": "Full &amp; complete"}},
        {"id": "2", "text": "Quoted", "referenced_posts": [{"type": "quoted", "id": "9"}]},
        {"id": "3", "text": "Reply", "in_reply_to_user_id": "9"},
        {"id": "4", "text": "RT @someone: Their writing"},
        {"id": "5", "text": "Short preview", "note_tweet": {"text": "Full &amp; complete"}},
    ]
    result = normalize_posts(posts, "example")
    assert result == [{"id": "1", "text": "Full & complete", "created_at": "",
                       "url": "https://x.com/example/status/1"}]


def test_import_is_bounded_and_does_not_save_credentials(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.x.com"
        assert request.headers["Authorization"] == "Bearer secret-token"
        if len(calls) == 1:
            return httpx.Response(200, json={"data": {"id": "123", "username": "example"}})
        assert request.url.path == "/2/users/123/tweets"
        assert request.url.params["exclude"] == "retweets,replies"
        return httpx.Response(200, json={
            "data": [{"id": "456", "text": "An original post."}],
            "meta": {"next_token": "more-pages"},
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await import_tweets(
                "@example", "secret-token", client=client, output_root=tmp_path
            )

    result = asyncio.run(run())
    assert len(calls) == 2
    assert result["has_more"] is True
    saved = next(tmp_path.glob("*.json")).read_text()
    assert "secret-token" not in saved and "Authorization" not in saved
    assert json.loads(saved)["posts"][0]["text"] == "An original post."


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_failed_import_has_no_cache_or_raw_error_leak(tmp_path, status):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"detail": "secret-token"})
        )) as client:
            await import_tweets("example", "secret-token", client=client, output_root=tmp_path)

    with pytest.raises(RuntimeError) as error:
        asyncio.run(run())
    assert "secret-token" not in str(error.value)
    assert not list(tmp_path.iterdir())


def test_high_model_scores_cannot_override_tweet_limit():
    def scores(value):
        return summarize_judgment({"answers": {
            **{k: {"type": "score", "score": value}
               for k in ("voice", "rhythm", "rhetoric", "structure", "economy")},
            **{k: {"type": "noul", "noul": 0.99}
               for k in ("facts_preserved", "no_invented_facts")},
        }})

    before = {"judgment": scores(2)}
    after = {"judgment": scores(4), "tweet_check": tweet_check("a" * 281)}
    accepted, reason = choose_version(before, after)
    assert not accepted
    assert "standard tweet" in reason
