"""Read public writing samples from X and validate standard-length tweet drafts."""

from __future__ import annotations

import html
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from twitter_text import parse_tweet


def tweet_check(text: str) -> dict:
    parsed = parse_tweet(text)
    return {"weighted_length": parsed.weightedLength, "limit": 280, "valid": parsed.valid}


def normalize_posts(posts: list[dict], username: str) -> list[dict]:
    """Use only the account's own standalone text, never expanded quoted content."""
    result, seen = [], set()
    for post in posts:
        if not isinstance(post, dict):
            continue
        post_id = str(post.get("id", ""))
        references = post.get("referenced_posts", post.get("referenced_tweets", []))
        if references or post.get("in_reply_to_user_id"):
            continue
        note = post.get("note_post") or post.get("note_tweet") or {}
        text = note.get("text") or post.get("text", "")
        if not isinstance(text, str) or not re.fullmatch(r"[0-9]{1,19}", post_id):
            continue
        text = html.unescape(text).strip()
        if not text or text.startswith("RT @") or text in seen:
            continue
        seen.add(text)
        result.append({
            "id": post_id, "text": text, "created_at": post.get("created_at", ""),
            "url": f"https://x.com/{username}/status/{post_id}",
        })
    return result


async def import_tweets(
    username: str, token: str, *, limit: int = 25, output_root: Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    username = username.strip().removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
        raise ValueError("Enter an X username, such as jerrycxu, without a URL.")
    if type(limit) is not int or not 5 <= limit <= 100:
        raise ValueError("Choose between 5 and 100 recent posts.")
    if not token.strip():
        raise ValueError("Add X_BEARER_TOKEN to the local .env file, then click Load my tweets.")
    owned_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30)

    async def get(path: str, params: dict | None = None) -> dict:
        try:
            response = await client.get(
                "https://api.x.com/2/" + path,
                params=params, headers={"Authorization": "Bearer " + token.strip()},
            )
        except httpx.TransportError:
            raise RuntimeError("X could not be reached. Retry when ready.") from None
        messages = {
            401: "X rejected the Bearer Token. Check X_BEARER_TOKEN in your local .env file.",
            402: "X requires API credits for this request. Check your X developer account.",
            403: "X denied this request. Check your app's read access and account visibility.",
            404: "X could not find this username or its public timeline.",
            429: "X rate-limited this request. Wait before trying again.",
        }
        if not response.is_success:
            raise RuntimeError(messages.get(
                response.status_code, f"X returned HTTP {response.status_code}."
            ))
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("X returned an unreadable response.") from None
        if not isinstance(data, dict) or data.get("errors"):
            raise RuntimeError("X returned an incomplete result. No import was saved.")
        return data

    try:
        user = (await get(f"users/by/username/{username}")).get("data", {})
        user_id = str(user.get("id", ""))
        if not re.fullmatch(r"[0-9]{1,19}", user_id):
            raise RuntimeError("X did not return a valid user ID.")
        page = await get(f"users/{user_id}/tweets", {
            "max_results": limit, "exclude": "retweets,replies",
            "post.fields": "created_at,lang,note_post",
            "expansions": "referenced_posts",
        })
        posts = page.get("data", [])
        if not isinstance(posts, list):
            raise RuntimeError("X did not return a readable posts list.")
        imported = {
            "username": username, "user_id": user_id,
            "fetched_at": datetime.now(UTC).isoformat(),
            "requested_limit": limit, "fetched_count": len(posts),
            "posts": normalize_posts(posts, username),
            "has_more": bool(page.get("meta", {}).get("next_token")),
            "note": "One page only. Replies, reposts, quotes, and duplicate text are excluded. "
                    "Authorship and use of AI are not inferred. Review samples before using them.",
        }
        if output_root is not None:
            output_root.mkdir(parents=True, exist_ok=True)
            path = output_root / f"{username}-{uuid.uuid4().hex[:8]}.json"
            path.write_text(json.dumps(imported, ensure_ascii=False, indent=2))
        return imported
    finally:
        if owned_client:
            await client.aclose()
