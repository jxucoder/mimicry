"""Build local voice evidence from official X API timeline responses."""

import html
import json
from datetime import UTC, datetime
from pathlib import Path


def build_profile(user, page, *, reference_limit=10000):
    expanded = {p["id"]: p for p in page.get("includes", {}).get("posts", [])}
    authors = {u["id"]: u.get("username", "") for u in page.get("includes", {}).get("users", [])}
    authored, reposts, seen = [], [], set()
    for post in page.get("data", []):
        refs = post.get("referenced_posts", post.get("referenced_tweets", []))
        repost = next((r for r in refs if r["type"] in ("retweeted", "reposted")), None)
        source = expanded.get(repost["id"]) if repost else post
        if not source or (not repost and str(source.get("author_id")) != str(user["id"])):
            continue
        text = html.unescape(
            (source.get("note_post") or source.get("note_tweet") or {}).get("text")
            or source.get("text", "")
        ).strip()
        if not text or (source["id"], bool(repost)) in seen:
            continue
        seen.add((source["id"], bool(repost)))
        kind = (
            "repost"
            if repost
            else "reply"
            if any(r["type"] == "replied_to" for r in refs)
            else "quote"
            if refs
            else "post"
        )
        author = authors.get(source.get("author_id"), "") if repost else user["username"]
        record = {
            "id": source["id"],
            "kind": kind,
            "author": author,
            "text": text,
            "created_at": source.get("created_at"),
            "url": f"https://x.com/{author or 'i'}/status/{source['id']}",
        }
        (reposts if repost else authored).append(record)
    selected, size = [], 0
    for post in authored:
        extra = len(post["text"]) + (7 if selected else 0)
        if size + extra <= reference_limit:
            selected.append(post)
            size += extra
    return {
        "username": user["username"],
        "user_id": user["id"],
        "source": "official_x_api",
        "fetched_at": datetime.now(UTC).isoformat(),
        "fetched_count": len(page.get("data", [])),
        "has_more": bool(page.get("meta", {}).get("next_token")),
        "authored": authored,
        "reposts": reposts,
        "selected_ids": [p["id"] for p in selected],
        "references": "\n\n---\n\n".join(p["text"] for p in selected),
        "note": (
            "Account authorship is verified by API author_id. Human versus AI authorship "
            "is not inferred. Reposts are weak preference evidence, not authored writing "
            "or proof of endorsement."
        ),
    }


def writer_preferences(config, references):
    """Match the exact imported voice before attaching another person's reposts."""
    path = config.get("X_VOICE_PROFILE")
    if not path or not references:
        return []
    try:
        profile = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    if profile.get("references") != references:
        return []
    # Keep full, short texts; the entire fetched corpus remains in the local profile.
    return [
        {"author": p["author"], "text": p["text"], "url": p["url"]}
        for p in profile.get("reposts", [])
        if len(p.get("text", "")) <= 600
    ][:8]


def local_profile(config, username=None):
    """Explicitly configured personal profile; never cross signed-in accounts."""
    if not config.get("X_VOICE_PROFILE"):
        return None
    try:
        profile = json.loads(Path(config["X_VOICE_PROFILE"]).read_text())
    except (OSError, ValueError):
        return None
    if username and username.lower() != profile.get("username", "").lower():
        return None
    references = profile.get("references")
    if not isinstance(references, str) or not 80 <= len(references) <= 12000:
        return None
    return profile
