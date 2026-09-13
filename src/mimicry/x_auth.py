"""X user authorization and timelines; credentials never enter browser payloads."""

from __future__ import annotations

import base64
import hashlib
import html
import math
import re
import secrets
import time
from urllib.parse import urlencode

import httpx

from mimicry.tweets import normalize_posts


class XClient:
    def __init__(self, config: dict, client: httpx.AsyncClient):
        self.config, self.client = config, client

    @property
    def configured(self) -> bool:
        return bool(self.config.get("X_CLIENT_ID") and self.config.get("X_CLIENT_SECRET"))

    def begin(self) -> tuple[str, dict]:
        if not self.configured:
            raise ValueError("Add X_CLIENT_ID and X_CLIENT_SECRET to the server .env file.")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        pending = {"state": secrets.token_urlsafe(32), "verifier": verifier,
                   "expires_at": time.time() + 600}
        url = "https://x.com/i/oauth2/authorize?" + urlencode({
            "response_type": "code", "client_id": self.config["X_CLIENT_ID"],
            "redirect_uri": self.config["X_REDIRECT_URI"],
            "scope": "tweet.read users.read", "state": pending["state"],
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
        })
        return url, pending

    async def request(self, path: str, *, method: str = "GET", **kwargs) -> httpx.Response:
        try:
            response = await self.client.request(method, "https://api.x.com/" + path, **kwargs)
        except httpx.TransportError:
            raise RuntimeError("X could not be reached. Try again.") from None
        if not response.is_success:
            messages = {
                400: "X rejected authorization. Sign in again and check the callback URL.",
                401: "X rejected this session. Sign in again or check the OAuth app credentials.",
                402: "X requires API credits for this request. Check the developer account.",
                403: "X denied this request. Check app read permissions and the callback URL.",
                429: "X rate-limited this request. Wait before loading again.",
            }
            raise RuntimeError(messages.get(
                response.status_code, f"X returned HTTP {response.status_code}."
            ))
        return response

    async def exchange(self, pending: dict, code: str) -> dict:
        response = await self.request(
            "2/oauth2/token", method="POST",
            auth=httpx.BasicAuth(self.config["X_CLIENT_ID"], self.config["X_CLIENT_SECRET"]),
            data={"grant_type": "authorization_code", "code": code,
                  "redirect_uri": self.config["X_REDIRECT_URI"],
                  "code_verifier": pending["verifier"]},
        )
        data = self.decode(response)
        token, expiry = data.get("access_token"), data.get("expires_in")
        if (not isinstance(token, str) or not token
                or str(data.get("token_type", "")).lower() != "bearer"
                or type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0):
            raise RuntimeError("X did not return complete authorization credentials.")
        if not {"tweet.read", "users.read"}.issubset(str(data.get("scope", "")).split()):
            raise RuntimeError("X did not grant the required read permissions. Sign in again.")
        return {"access_token": token, "expires_at": time.time() + expiry}

    async def revoke(self, auth: dict) -> None:
        await self.request(
            "2/oauth2/revoke", method="POST",
            auth=httpx.BasicAuth(self.config["X_CLIENT_ID"], self.config["X_CLIENT_SECRET"]),
            data={"token": auth["access_token"], "token_type_hint": "access_token"},
        )

    async def get(self, path: str, auth: dict, params: dict | None = None) -> dict:
        if auth["expires_at"] <= time.time():
            raise RuntimeError("Your X session expired. Sign in again.")
        response = await self.request(
            path, params=params, headers={"Authorization": "Bearer " + auth["access_token"]}
        )
        return self.decode(response)

    @staticmethod
    def decode(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("X returned an unreadable response.") from None
        if not isinstance(data, dict) or data.get("errors"):
            raise RuntimeError("X returned incomplete data. Please try again.")
        return data

    async def profile(self, auth: dict) -> dict:
        user = (await self.get("2/users/me", auth)).get("data", {})
        if not re.fullmatch(r"[0-9]{1,19}", str(user.get("id", ""))):
            raise RuntimeError("X could not confirm your identity.")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", user.get("username", "")):
            raise RuntimeError("X did not return a valid username.")
        return {key: user.get(key, "") for key in ("id", "name", "username")}

    async def own_posts(self, user: dict, auth: dict) -> dict:
        page = await self.get(f"2/users/{user['id']}/tweets", auth, {
            "max_results": 25, "exclude": "retweets,replies",
            "post.fields": "created_at,lang,note_post", "expansions": "referenced_posts",
        })
        return {
            "posts": normalize_posts(page.get("data", []), user["username"]),
            "has_more": bool(page.get("meta", {}).get("next_token")),
        }

    async def feed(self, user: dict, auth: dict) -> dict:
        page = await self.get(f"2/users/{user['id']}/timelines/reverse_chronological", auth, {
            "max_results": 25, "exclude": "retweets,replies",
            "post.fields": "created_at,lang,note_post",
            "expansions": "author_id", "user.fields": "name,username",
        })
        authors = {str(u["id"]): u for u in page.get("includes", {}).get("users", [])}
        posts = []
        for post in page.get("data", []):
            author = authors.get(str(post.get("author_id", "")), {})
            username = author.get("username") or post.get("username", "")
            post_id = str(post.get("id", ""))
            if not re.fullmatch(r"[0-9]{1,19}", post_id):
                continue
            if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
                username = ""
            note = post.get("note_post") or post.get("note_tweet") or {}
            text = note.get("text") or post.get("text", "")
            if not isinstance(text, str) or not text.strip():
                continue
            posts.append({
                "id": post_id, "text": html.unescape(text),
                "username": username, "name": author.get("name") or username or "X user",
                "created_at": post.get("created_at", ""),
                "url": f"https://x.com/{username or 'i'}/status/{post_id}",
            })
        return {"posts": posts, "kind": "reverse_chronological",
                "has_more": bool(page.get("meta", {}).get("next_token"))}
