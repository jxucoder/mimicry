import json

from mimicry.x_voice import build_profile, local_profile, writer_preferences


def test_profile_keeps_own_reply_and_quote_but_separates_reposts(tmp_path):
    user = {"id": "1", "username": "owner"}
    own = "These are my actual words, written in my own voice. " * 2
    page = {
        "data": [
            {
                "id": "10",
                "author_id": "1",
                "text": own,
                "referenced_posts": [{"id": "90", "type": "replied_to"}],
            },
            {
                "id": "11",
                "author_id": "1",
                "text": "My quote comment.",
                "referenced_posts": [{"id": "90", "type": "quoted"}],
            },
            {
                "id": "12",
                "author_id": "1",
                "text": "RT truncated",
                "referenced_posts": [{"id": "90", "type": "reposted"}],
            },
            {"id": "13", "author_id": "2", "text": "Not written by owner"},
        ],
        "includes": {
            "posts": [{"id": "90", "author_id": "2", "text": "Their complete post."}],
            "users": [{"id": "2", "username": "other"}],
        },
    }
    profile = build_profile(user, page)
    assert [p["kind"] for p in profile["authored"]] == ["reply", "quote"]
    assert "Their complete post." not in profile["references"]
    assert profile["reposts"][0]["author"] == "other"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    config = {"X_VOICE_PROFILE": str(path)}
    assert len(writer_preferences(config, profile["references"])) == 1
    assert writer_preferences(config, "a different voice") == []
    assert local_profile(config, "other") is None
    assert local_profile(config, "owner")["username"] == "owner"


def test_profile_limit_keeps_complete_samples_and_missing_reposts_are_not_voice():
    user = {"id": "1", "username": "owner"}
    page = {
        "data": [
            {"id": "10", "author_id": "1", "text": "a" * 50},
            {"id": "11", "author_id": "1", "text": "b" * 50},
            {
                "id": "12",
                "author_id": "1",
                "text": "RT unavailable",
                "referenced_posts": [{"id": "90", "type": "reposted"}],
            },
        ]
    }
    profile = build_profile(user, page, reference_limit=80)
    assert profile["references"] == "a" * 50
    assert profile["selected_ids"] == ["10"]


def test_repost_context_is_writer_only_and_does_not_mutate_judge_state(tmp_path):
    import asyncio

    from mimicry.engine import Providers

    path = tmp_path / "profile.json"
    references = "My own style samples. " * 5
    path.write_text(
        json.dumps(
            {
                "references": references,
                "reposts": [
                    {
                        "author": "another",
                        "text": "A reposted observation.",
                        "url": "https://x.com/another/status/90",
                    }
                ],
            }
        )
    )
    api = Providers({"X_VOICE_PROFILE": str(path), "STYLE_WRITER_MODEL": "test"}, tmp_path, None)
    calls = []

    async def request(provider, stage, payload):
        calls.append(payload)
        return {
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "A rewrite."}]}
            ],
        }

    api.request = request
    state = {"references": references, "source_text": "My thought.", "current_text": "My thought."}
    assert asyncio.run(api.write(state, "test")) == "A rewrite."
    assert json.loads(calls[0]["input"])["reposted_context"][0]["author"] == "another"
    assert "reposted_context" not in state
