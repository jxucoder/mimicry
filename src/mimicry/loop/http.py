"""Versioned browser boundary for the personal loop; no provider secrets or raw traces."""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route

from mimicry.loop import LoopService
from mimicry.loop.models import WRITING_MODES, check_context, digest

CONTRACT_VERSION = 1


def public_run(run: dict) -> dict:
    keys = ("run_id", "status", "writing_mode", "source_text", "intent_version", "context",
            "selected", "review_version", "review_required", "selected_eligible", "stop_reason")
    result = {k: run[k] for k in keys if k in run}
    result["versions"] = {name: {k: v[k] for k in
        ("text", "phase", "based_on", "checks", "eligible", "tweet_check", "features") if k in v}
        for name, v in run.get("versions", {}).items()}
    result["criteria"] = run.get("criteria", {})
    result["steps"] = [{k: step[k] for k in
        ("action", "version", "based_on", "passed", "hard_passed", "failed", "uncertain",
         "skipped_rules", "failures", "reason", "selected", "duplicate_of") if k in step}
        for step in run.get("steps", [])]
    return result


def public_learning(result: dict) -> dict:
    event = result["feedback"]
    proposal, validation = result.get("proposal"), result.get("validation")
    return {
        "feedback_id": event["id"], "run_id": event["run_id"],
        "attribution_status": event["attribution_status"],
        "style_eligible": event["style_eligible"],
        "proposal": ({k: proposal[k] for k in ("id", "status", "rule_id") if k in proposal}
                     if proposal else None),
        "validation": ({k: validation[k] for k in ("status", "promoted")}
                       if validation else None),
        "suspended_rule_ids": result.get("suspended", []),
    }


def parse_rewrite(data: dict) -> dict:
    allowed = {"request_id", "source_text", "references", "feed_context", "writing_mode",
               "context", "output_format"}
    if set(data) - allowed:
        raise ValueError("Unknown rewrite fields. Identity and provider settings are server-owned.")
    mode = data.get("writing_mode", "refine_personalize")
    if not isinstance(mode, str) or mode not in WRITING_MODES:
        raise ValueError("Choose refine_personalize, personalize, or refine.")
    result = {"writing_mode": mode}
    for key, minimum in (("source_text", 10), ("references", 0), ("feed_context", 0)):
        text = data.get(key, "")
        if not isinstance(text, str) or not minimum <= len(text.strip()) <= 12000:
            raise ValueError(f"{key} must contain {minimum}–12,000 characters.")
        result[key] = text
    if mode != "refine" and len(result["references"].strip()) < 80:
        raise ValueError("Select at least 80 characters of your writing as references.")
    if mode == "refine":
        result["references"] = ""
    result["context"] = check_context(data.get("context", {"language": "en", "purpose": "general"}))
    result["output_format"] = data.get("output_format", "tweet")
    if result["output_format"] not in ("tweet", "text"):
        raise ValueError("Choose tweet or text output.")
    return result


def parse_feedback(data: dict) -> tuple[str, dict]:
    allowed = {"request_id", "run_id", "against_version", "intent_version", "edited_text",
               "chosen_version", "explanation", "intent_changed"}
    if set(data) - allowed:
        raise ValueError("Unknown feedback fields. Evidence provenance is server-owned.")
    for key in ("run_id", "against_version", "intent_version"):
        if not isinstance(data.get(key), str) or not 1 <= len(data[key]) <= 200:
            raise ValueError(f"A bounded {key} is required.")
    if (data.get("edited_text") is None) == (data.get("chosen_version") is None):
        raise ValueError("Send edited_text or chosen_version, not both.")
    if "edited_text" in data and (not isinstance(data["edited_text"], str)
                                  or not 1 <= len(data["edited_text"].strip()) <= 12000):
        raise ValueError("Edited text must contain 1–12,000 characters.")
    if data.get("chosen_version") is not None and (
            not isinstance(data["chosen_version"], str) or len(data["chosen_version"]) > 200):
        raise ValueError("Invalid chosen version.")
    if not isinstance(data.get("explanation", ""), str) or len(data.get("explanation", "")) > 4000:
        raise ValueError("Explanation must be text within 4,000 characters.")
    if type(data.get("intent_changed", False)) is not bool:
        raise ValueError("intent_changed must be a boolean.")
    return data["run_id"], {k: v for k, v in data.items() if k not in ("run_id", "request_id")}


def create_loop_routes(*, config, db_path: Path, csrf_session, signed_in, body, tasks):
    """Use the host app's session, origin/CSRF checks, HTTP client and task lifecycle."""
    def owner(session):
        if session.get("user") or session.get("auth"):
            signed_in(session)
            return "x:" + str(session["user"]["id"])
        # A selected @handle or request field never grants access to an account's memory.
        return "local:" + session.setdefault("loop_owner", secrets.token_urlsafe(24))

    def service(request):
        return LoopService(config=config, db_path=db_path, client=request.app.state.client)

    def view(job):
        return {k: job[k] for k in ("id", "kind", "status", "revision", "progress", "result")}

    async def profile(request):
        session = csrf_session(request)
        owner_id = owner(session)
        output_format = request.query_params.get("output_format", "tweet")
        if output_format not in ("tweet", "text"):
            raise ValueError("Choose tweet or text output.")
        async with service(request) as loop:
            profile = loop.inspect(owner_id, output_format=output_format)
        policy = profile["active_policy"]
        return JSONResponse({"contract_version": CONTRACT_VERSION, "modes": WRITING_MODES,
            "policy_id": policy["id"], "model": policy["model"], "rules": policy["rules"],
            "output_format": output_format,
            "proposals": [{k: p[k] for k in ("id", "status", "rule_id", "currently_active")
                           if k in p} for p in profile["proposals"]],
            "jobs": [{k: j[k] for k in ("id", "kind", "status", "request_id")}
                     for j in session.get("loop_jobs", {}).values() if j["owner"] == owner_id],
            "limits": {"drafts": 1, "repairs": 1, "active_rules": 5}})

    async def start(request):
        session = csrf_session(request)
        owner_id = owner(session)
        data = await body(request)
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
            raise ValueError("Supply request_id and reuse it when retrying the same request.")
        kind = request.path_params["kind"]
        if kind not in ("rewrite", "feedback"):
            raise HTTPException(404)
        parsed = parse_rewrite(data) if kind == "rewrite" else parse_feedback(data)
        jobs = session.setdefault("loop_jobs", {})
        fingerprint = digest({"kind": kind, "owner": owner_id, "payload": data})
        existing = next((j for j in jobs.values() if j["request_id"] == request_id), None)
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise HTTPException(409, "This request_id was already used with different data.")
            return JSONResponse({"contract_version": CONTRACT_VERSION, **view(existing)},
                                status_code=202)
        if session.get("task") and not session["task"].done():
            raise HTTPException(409, "A task is already running in this session.")
        # Bound session memory without evicting idempotency keys and permitting duplicate calls.
        if len(jobs) >= 100:
            raise HTTPException(409, "This session reached its job limit. Start a new session.")
        for key in ("OPENAI_API_KEY", "TYPESAFE_API_KEY"):
            if not config.get(key):
                raise HTTPException(400, f"Set {key} in the server environment.")
        if kind == "feedback":
            async with service(request) as loop:
                try:
                    run = loop.store.get(owner_id, "run", parsed[0])
                except ValueError:
                    raise HTTPException(404, "This run is not available to your account.") from None
                if run["status"] not in ("complete", "partial", "cancelled"):
                    raise HTTPException(409, "Wait for the run to stop before sending feedback.")
                fields = parsed[1]
                if fields["intent_version"] != run["intent_version"]:
                    raise HTTPException(409, "This edit belongs to an outdated draft intent.")
                if fields["against_version"] not in run["versions"] or (
                    fields.get("chosen_version") is not None
                    and fields["chosen_version"] not in run["versions"]
                ):
                    raise ValueError("Choose a version from this run.")
        job = {"id": secrets.token_urlsafe(24), "kind": kind, "status": "running",
               "revision": 0, "progress": "Starting…", "result": None,
               "request_id": request_id, "fingerprint": fingerprint, "owner": owner_id}
        jobs[job["id"]] = job

        def update(**fields):
            job.update(fields)
            job["revision"] += 1

        def snapshot(run):
            action = (run.get("steps") or [{}])[-1].get("action", "WRITE")
            message = {"WRITE": "Writing your draft…", "CHECKING": "Checking the draft…",
                       "CHECK": "Checking the draft…",
                       "REPAIR": "Fixing the failed checks…", "REVIEW": "Ready for your review.",
                       "ACCEPT": "Draft ready.", "STOP": "Finished."}.get(action, "Working…")
            update(result=public_run(run), progress=message)

        async def execute():
            try:
                async with service(request) as loop:
                    if kind == "rewrite":
                        result = await loop.rewrite(owner_id, **parsed, on_snapshot=snapshot)
                        update(result=public_run(result), status=result["status"],
                               progress="Review needed." if result["review_required"] else "Ready.")
                    else:
                        update(progress="Learning from your edit…")
                        result = await loop.learn_from_feedback(
                            owner_id, parsed[0], **parsed[1],
                            event_id=digest({"run": parsed[0], "request": request_id})[:32])
                        clean = public_learning(result)
                        failed = (clean["attribution_status"] == "failed"
                                  or (clean["proposal"] or {}).get("status") == "failed"
                                  or (clean["validation"] or {}).get("status") == "failed")
                        update(result=clean, status="partial" if failed else "complete",
                               progress=("Edit saved; learning incomplete."
                                         if failed else "Edit saved."))
            except asyncio.CancelledError:
                update(status="cancelled", progress="Stopped. Your saved draft is still available.")
                raise
            except (ValueError, RuntimeError):
                update(status="failed", progress="Could not complete this task. Draft retained.")
            except Exception:
                update(status="failed", progress="Task stopped unexpectedly. Draft retained.")

        task = asyncio.create_task(execute())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        session["task"] = task
        job["task"] = task
        return JSONResponse({"contract_version": CONTRACT_VERSION, **view(job)}, status_code=202)

    async def get_job(request):
        session = csrf_session(request)
        owner_id = owner(session)
        job = session.get("loop_jobs", {}).get(request.path_params["job_id"])
        if job is None or job["owner"] != owner_id:
            raise HTTPException(404, "This task is not available in your session.")
        return JSONResponse({"contract_version": CONTRACT_VERSION, **view(job)})

    async def cancel(request):
        session = csrf_session(request)
        owner_id = owner(session)
        job = session.get("loop_jobs", {}).get(request.path_params["job_id"])
        if job is None or job["owner"] != owner_id:
            raise HTTPException(404, "This task is not available in your session.")
        if not job["task"].done():
            job["task"].cancel()
            await asyncio.gather(job["task"], return_exceptions=True)
            # A task cancelled before its first instruction cannot execute its own handler.
            if job["status"] == "running":
                job.update(status="cancelled", progress="Stopped.", revision=job["revision"] + 1)
        return JSONResponse({"contract_version": CONTRACT_VERSION, **view(job)})

    return [Route("/api/loop/profile", profile),
            Route("/api/loop/jobs/{job_id}", get_job),
            Route("/api/loop/jobs/{job_id}/cancel", cancel, methods=["POST"]),
            Route("/api/loop/{kind}", start, methods=["POST"])]
