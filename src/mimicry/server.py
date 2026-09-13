"""Local writing workspace with server-side X OAuth 2.0 sessions."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from mimicry.diff import edit_difference
from mimicry.engine import ROOT, WRITING_MODES, input_errors, questions, rewrite, settings
from mimicry.examples import DRAFT, PRESETS
from mimicry.extension_identity import EXTENSION_ORIGIN
from mimicry.loop.http import create_loop_routes
from mimicry.x_auth import XClient
from mimicry.x_voice import local_profile

COOKIE = "mimicry_session"
SESSION_TTL = 4 * 60 * 60
STATIC = Path(__file__).with_name("web")


def create_app(config=None, *, client=None, output_root=None, rewrite_fn=rewrite):
    config = dict(settings() if config is None else config)
    config.setdefault("X_REDIRECT_URI", "http://127.0.0.1:2719/auth/x/callback")
    redirect = urlsplit(config["X_REDIRECT_URI"])
    if (redirect.scheme != "http" or redirect.hostname not in ("127.0.0.1", "localhost")
            or redirect.path != "/auth/x/callback" or redirect.query or redirect.fragment):
        raise ValueError("This local server requires a loopback HTTP X_REDIRECT_URI.")
    origin = f"{redirect.scheme}://{redirect.netloc}"
    sessions, tasks = {}, set()

    @asynccontextmanager
    async def lifespan(app):
        app.state.client = client or httpx.AsyncClient(timeout=120, follow_redirects=False)
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        sessions.clear()
        if client is None:
            await app.state.client.aclose()

    def x(request):
        return XClient(config, request.app.state.client)

    def create_session():
        sid = secrets.token_urlsafe(32)
        session = {"csrf": secrets.token_urlsafe(32), "expires_at": time.time() + SESSION_TTL}
        sessions[sid] = session
        return sid, session

    def session_for(request, *, required=True):
        now = time.time()
        for sid, session in list(sessions.items()):
            if session["expires_at"] <= now:
                sessions.pop(sid)
        session = sessions.get(request.cookies.get(COOKIE))
        if not session and required:
            raise HTTPException(401, "This local session expired. Reload the page.")
        return session

    def csrf_session(request):
        session = session_for(request)
        if (request.headers.get("origin") not in (origin, EXTENSION_ORIGIN)
                or not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), session["csrf"])):
            raise HTTPException(403, "This request could not be verified. Reload the page.")
        return session

    def signed_in(session):
        if not session.get("auth") or session["auth"]["expires_at"] <= time.time():
            session.pop("auth", None)
            session.pop("user", None)
            session.pop("posts", None)
            session.pop("feed", None)
            raise HTTPException(401, "Sign in with X to load your account.")

    def cookie(response, sid):
        response.set_cookie(COOKIE, sid, httponly=True, samesite="lax", max_age=SESSION_TTL)
        return response

    async def body(request):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 200_000:
                raise HTTPException(413, "This request is too large.")
            chunks.append(chunk)
        try:
            data = json.loads(b"".join(chunks) or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Send a valid JSON object.") from None
        if not isinstance(data, dict):
            raise HTTPException(400, "Send a valid JSON object.")
        return data

    def workspace(data):
        result = {}
        for key in ("references", "source_text", "feed_context"):
            value = data.get(key, "")
            if not isinstance(value, str) or len(value) > 12000:
                raise HTTPException(400, f"{key} must be text within 12,000 characters.")
            result[key] = value
        result["output_format"] = data.get("output_format", "tweet")
        if result["output_format"] not in ("tweet", "text"):
            raise HTTPException(400, "Choose a single tweet or general writing.")
        result["writing_mode"] = data.get("writing_mode", "refine_personalize")
        if result["writing_mode"] not in WRITING_MODES:
            raise HTTPException(400, "Choose one of the three writing modes.")
        return result

    async def home(request):
        if str(request.base_url).rstrip("/") != origin:
            return RedirectResponse(origin + "/", status_code=303)
        return FileResponse(STATIC / "index.html")

    async def asset(request):
        name = request.path_params["name"]
        if name not in ("app.js", "app.css"):
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    async def inline_demo(request):
        return FileResponse(STATIC / "inline" / "demo.html")

    async def inline_asset(request):
        name = request.path_params["name"]
        if name not in ("core.js", "composer.js", "composer.css", "demo.js", "demo.css"):
            raise HTTPException(404)
        return FileResponse(STATIC / "inline" / name)

    async def bootstrap(request):
        session = session_for(request, required=False)
        sid = request.cookies.get(COOKIE)
        if not session:
            sid, session = create_session()
        if session.get("auth"):
            try:
                signed_in(session)
            except HTTPException:
                session["notice"] = "Your X session expired. Sign in again to load more posts."
        profile = local_profile(config, session.get("user", {}).get("username"))
        profile_info = None
        if profile:
            profile_info = {"username": profile["username"], "source": profile["source"],
                            "authored_count": len(profile["selected_ids"]),
                            "repost_count": len(profile["reposts"])}
            if "voice_imported" not in session:
                saved = session.setdefault("workspace", {})
                if not saved.get("references"):
                    saved.update(references=profile["references"])
                    saved.setdefault("source_text", "")
                    saved.setdefault("feed_context", "")
                    saved.setdefault("output_format", "tweet")
                    saved.setdefault("writing_mode", "refine_personalize")
                session["voice_imported"] = True
        return cookie(JSONResponse({
            "csrf": session["csrf"], "user": session.get("user"),
            "x_configured": x(request).configured,
            "models_ready": bool(config.get("OPENAI_API_KEY") and config.get("TYPESAFE_API_KEY")),
            "notice": session.pop("notice", None), "workspace": session.get("workspace", {}),
            "posts": session.get("posts"), "feed": session.get("feed"),
            "voice_profile": profile_info,
            "job_id": session.get("job", {}).get("id"),
            "criteria": {mode: questions(mode) for mode in ("tweet", "text")},
            "examples": {"references": PRESETS["Builder tweets"], "source_text": DRAFT},
        }), sid)

    async def start(request):
        session = csrf_session(request)
        if session.get("auth"):
            raise HTTPException(409, "Sign out before connecting another X account.")
        data = workspace(await body(request))
        url, pending = x(request).begin()
        session.update(pending=pending, workspace=data)
        return JSONResponse({"url": url})

    async def callback(request):
        session = session_for(request, required=False)
        pending = session.get("pending") if session else None
        state = request.query_params.get("state", "")
        valid = (pending and state.isascii() and pending["expires_at"] > time.time()
                 and secrets.compare_digest(state, pending["state"])
                 and len(request.query_params.getlist("state")) == 1)
        if not valid:
            if session:
                session["notice"] = "The X sign-in link expired or is invalid. Start sign-in again."
            return RedirectResponse("/?signin=invalid", status_code=303)
        session.pop("pending")  # Consume before exchanging; callbacks cannot be replayed.
        if request.query_params.get("error"):
            session["notice"] = "X sign-in was cancelled. Your draft is still here."
            return RedirectResponse("/", status_code=303)
        codes = request.query_params.getlist("code")
        if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
            session["notice"] = "X did not return an authorization code. Start sign-in again."
            return RedirectResponse("/", status_code=303)
        try:
            auth = await x(request).exchange(pending, codes[0])
            user = await x(request).profile(auth)
        except (ValueError, RuntimeError) as error:
            session["notice"] = str(error)
            return RedirectResponse("/", status_code=303)
        sessions.pop(request.cookies.get(COOKIE), None)
        sid, new_session = create_session()
        new_session.update(auth=auth, user=user, workspace=session.get("workspace", {}))
        return cookie(RedirectResponse("/", status_code=303), sid)

    async def logout(request):
        session = csrf_session(request)
        sessions.pop(request.cookies.get(COOKIE), None)
        if session.get("task"):
            session["task"].cancel()
        message = "Signed out. Local account data has been cleared."
        if session.get("auth"):
            try:
                await x(request).revoke(session["auth"])
            except (RuntimeError, ValueError):
                message += (
                    " X could not confirm token revocation; manage access in X Connected apps."
                )
        response = JSONResponse({"message": message})
        response.delete_cookie(COOKIE)
        return response

    async def timeline(request):
        session = csrf_session(request)
        signed_in(session)
        kind = request.path_params["kind"]
        if kind not in ("posts", "feed"):
            raise HTTPException(404)
        if session.get("loading_" + kind):
            raise HTTPException(409, "This timeline is already loading.")
        session["loading_" + kind] = True
        try:
            api = x(request)
            data = await (api.own_posts(session["user"], session["auth"]) if kind == "posts"
                          else api.feed(session["user"], session["auth"]))
            session[kind] = data
            return JSONResponse(data)
        finally:
            session["loading_" + kind] = False

    async def run_rewrite(request):
        session = csrf_session(request)
        if session.get("task") and not session["task"].done():
            raise HTTPException(409, "A rewrite is already running in this session.")
        data = workspace(await body(request))
        errors = input_errors(
            data["references"], data["source_text"], writing_mode=data["writing_mode"],
        )
        if errors:
            return JSONResponse({"error": " ".join(errors.values())}, status_code=400)
        if not config.get("OPENAI_API_KEY") or not config.get("TYPESAFE_API_KEY"):
            raise HTTPException(400, "Add OPENAI_API_KEY and TYPESAFE_API_KEY to the server .env.")
        session["workspace"] = data
        job = {"id": secrets.token_urlsafe(24), "status": "running",
               "progress": "Starting Luna…", "result": None}
        session["job"] = job

        async def execute():
            try:
                result = await rewrite_fn(
                    data["references"], data["source_text"], output_format=data["output_format"],
                    feed_context=data["feed_context"], config=config,
                    writing_mode=data["writing_mode"],
                    output_root=output_root or ROOT / "runs" / "mimicry",
                    client=request.app.state.client,
                    progress=lambda message: job.update(progress=message),
                    on_snapshot=lambda result: job.update(result=result),
                )
                job.update(result=result, status=result["status"],
                           progress=result.get("stop_reason", result.get("decision", "Finished.")))
            except asyncio.CancelledError:
                job.update(status="cancelled", progress="The rewrite was stopped.")
                raise
            except Exception:
                job.update(status="failed", progress="The rewrite stopped unexpectedly. Try again.")

        task = asyncio.create_task(execute())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        session["task"] = task
        return JSONResponse({"id": job["id"]}, status_code=202)

    async def job_result(request):
        session = session_for(request)
        job = session.get("job", {})
        if job.get("id") != request.path_params["job_id"]:
            raise HTTPException(404, "This rewrite is not available in your session.")
        result = job.get("result")
        if result:
            result = {key: value for key, value in result.items()
                      if key not in ("calls", "output_dir")}
        return JSONResponse({**job, "result": result, "diffs": {
            key: edit_difference(result["source_text"], version["text"])
            for key, version in (result or {}).get("versions", {}).items()
        }})

    async def handled_error(request, error):
        status = error.status_code if isinstance(error, HTTPException) else 400
        message = error.detail if isinstance(error, HTTPException) else str(error)
        return JSONResponse({"error": message}, status_code=status)

    async def security(request, call_next):
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                       "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                                       "frame-ancestors 'none'; form-action 'self'",
        })
        return response

    app = Starlette(routes=[
        Route("/", home), Route("/assets/{name}", asset), Route("/api/session", bootstrap),
        Route("/inline-demo", inline_demo), Route("/inline-assets/{name}", inline_asset),
        Route("/auth/x/start", start, methods=["POST"]), Route("/auth/x/callback", callback),
        Route("/auth/x/logout", logout, methods=["POST"]),
        Route("/api/x/{kind}", timeline, methods=["POST"]),
        Route("/api/rewrite", run_rewrite, methods=["POST"]),
        Route("/api/jobs/{job_id}", job_result),
        *create_loop_routes(config=config,
            db_path=(output_root or ROOT / "runs" / "personal-loop") / "loop.sqlite3",
            csrf_session=csrf_session, signed_in=signed_in, body=body, tasks=tasks),
    ], lifespan=lifespan, exception_handlers={
        HTTPException: handled_error, ValueError: handled_error, RuntimeError: handled_error,
    })
    app.state.sessions = sessions
    app.add_middleware(BaseHTTPMiddleware, dispatch=security)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    return app


def main():
    config = settings()
    redirect = urlsplit(config["X_REDIRECT_URI"])
    # Suppress access logs so authorization codes never appear in terminal history.
    uvicorn.run(create_app(config), host="127.0.0.1", port=redirect.port or 2719,
                access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
