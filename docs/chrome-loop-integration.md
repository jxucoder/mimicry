# Chrome integration: loop API v1

After restarting the local server to load the new routes, the personal loop is available
alongside the existing app API at
`http://127.0.0.1:2719/api/loop`. The old `/api/rewrite` endpoint is unchanged. Use the
new endpoints for the bounded personal loop and edit-driven learning.

Import `integrations/chrome/loop-client.mjs` into the extension's service-worker build.
It is a dependency-free ES module; bundle it if the current worker is a classic script.
Keep API access in the worker, not the X page's content script. The existing extension
origin and local-server permissions remain in effect. No provider or X API credentials
belong in Chrome storage or messages.

## Minimal worker integration

```js
import {createLoopClient, suggestionFor} from './loop-client.mjs';

const loop = createLoopClient();
const profile = await loop.connect(); // Local session cookie + CSRF; returns modes and jobs.
const request = {
  request_id: crypto.randomUUID(), // Retain and reuse if the response is lost.
  source_text: composerText,
  references: selectedOwnTweets.join('\n\n'),
  writing_mode: 'refine_personalize',
  output_format: 'text', // Preserve the full post; do not force a 280-character summary.
  context: {language: 'en', purpose: 'build_update'},
};
const started = await loop.startRewrite(request);
const job = await loop.wait(started.id, {onUpdate: renderProgress});
if (job.result) {
  const suggestion = suggestionFor(job.result, currentComposerText);
  renderSuggestion(suggestion);
  // Insert only after an explicit user click AND suggestion.canApply.
}
```

`request_id` is scoped to the server session. Repeating the same request returns the
same job; changing its payload returns 409. No automatic retry creates a new request
ID. One task may run per session, including legacy rewrite tasks. The session allows
100 jobs and expires after four hours. Server restart loses
sessions/jobs; persisted loop runs and account memory remain in SQLite. A reconnect
may therefore require sign-in again; do not blindly replay old requests after a new
session has been created.

Chrome service-worker restart does not cancel a server job. After `connect()`, use
`profile.jobs` to find and resume polling the job. Each entry has `id`, `kind`, `status`
and `request_id`. Store a job's originating tab/composer identity in `chrome.storage.session`
if necessary. Never apply its text to a different composer or to changed source text.

## Modes

Inline Chrome rewriting and the worker client default to full-post `text` output.
This preserves long arguments instead of forcing them under 280 weighted characters.
To explicitly request a standard tweet, construct the client with
`createLoopClient({outputFormat: 'tweet'})`. Its profile and rewrites use the same format.
The raw HTTP rewrite endpoint retains its existing `tweet` default, so callers that
bypass the client must send `output_format: "text"` for full posts.

| `writing_mode` | Label | References | Personal learning |
| --- | --- | --- | --- |
| `refine_personalize` | AI refine + Personalize | At least 80 characters | Enabled |
| `personalize` | Personalize only | At least 80 characters | Enabled |
| `refine` | AI refine only | Not required or used | Disabled |

All modes preserve meaning and current requirements. The loop generates one draft,
checks it with TypeSafe, and makes at most one targeted repair. Intermediate
probabilities produce a review request, not an unbounded retry loop.

## HTTP contract

First GET `/api/session` to obtain the local session cookie and `csrf` value. Every
`/api/loop` request requires that cookie, `x-csrf-token`, and the browser-supplied
Origin matching the local app or bundled extension. The SDK handles cookies and CSRF.
Do not accept owner IDs, X handles, model names or credentials from page content.
The server derives identity from verified X login or an isolated local session.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/loop/profile?output_format=text` | Modes, active rules, proposal statuses and resumable session jobs |
| POST | `/api/loop/rewrite` | 202 background rewrite job |
| POST | `/api/loop/feedback` | 202 background edit/learning job |
| GET | `/api/loop/jobs/{id}` | Current job and result snapshot |
| POST | `/api/loop/jobs/{id}/cancel` | Stops that job; returns its terminal snapshot |

Every successful response has `contract_version: 1`. A job contains:

```json
{
  "contract_version": 1,
  "id": "opaque-job-id",
  "kind": "rewrite",
  "status": "running",
  "revision": 3,
  "progress": "Checking the draft…",
  "result": null
}
```

Statuses are `running`, `complete`, `partial`, `failed`, or `cancelled`. `revision`
increases as snapshots change. Polling at 500 ms is sufficient. `result` can be present
while running and remain available after cancellation or a partial failure. A completed
job can still require review; completion is not approval. The client `wait()` returns
on every terminal status, not just success. A polling timeout or aborted polling does
not stop the server job; call `cancel(id)` explicitly.

A rewrite result includes:

- `run_id`, `intent_version`, `writing_mode`, `source_text`, `context`.
- `versions.original`, `versions.draft`, and optional `versions.repair`; each has text
  and, once checked, `checks`, `features` (Noul probabilities), and tweet-length results.
- `selected`, `selected_eligible`, `review_required`, and `review_version`.
- `criteria` with the actual TypeSafe questions, plus `steps` and `stop_reason`.

Use `selected` only when the run is complete, `selected_eligible` is true and
`review_required` is false. Otherwise show `review_version` as tentative and preserve
the user's editor. `suggestionFor()` also rejects stale results when the current
composer text differs from `source_text`. Do not render the old `style_mean`, `improved`
or `rewrite_1` fields; this controller uses conditions, not a style score.

Returned results omit reference corpora, provider requests/responses, API usage,
filesystem paths and raw learning records. The actual criteria and per-check
probabilities are intentionally visible so the user can inspect the decision.

Errors use `{"error": "message"}`: 400 invalid input, 401 expired session/sign-in,
403 unverified origin/CSRF, 404 unavailable job/run, 409 active job, conflicting request
ID or stale intent, and 413 oversized body. Generation/learning failures after 202 are
reported in the job status with a safe message.

## User edits close the learning loop

When the user explicitly saves an edit or chooses a version, send the version they
actually saw, the same `run_id` and `intent_version`, and either `edited_text` or
`chosen_version`:

```js
const started = await loop.startFeedback({
  request_id: crypto.randomUUID(),
  run_id: displayedRun.run_id,
  against_version: displayedVersion,
  intent_version: displayedRun.intent_version,
  edited_text: editedText,
  explanation: optionalUserExplanation,
  intent_changed: false,
});
const learned = await loop.wait(started.id, {onUpdate: renderLearningProgress});
```

Do not interpret programmatic insertion, automatic rewrites, or every keystroke as a
human preference. If the user changed facts or intent, set `intent_changed: true`.
If the user writes a new draft while a job runs, start a new rewrite rather than
attaching its text as feedback to an old intent.

A feedback result contains `feedback_id`, `run_id`, `attribution_status`,
`style_eligible`, optional `proposal: {id, status, rule_id}`, optional
`validation: {status, promoted}`, and `suspended_rule_ids`. A first edit typically
produces a `shadow` proposal; that is not an active preference. A later distinct edit
must support it before activation. Refresh `profile()` after learning to update the
criteria panel. Refine-only edits are saved with `attribution_status: "refine_only"`
and never teach personal style.

## Verification and remaining Chrome work

```bash
uv run --extra dev pytest -q tests/test_loop_http.py tests/test_personal_loop.py
node --test tests/test_loop_client.mjs
uv run python scripts/smoke_loop_http.py --live
```

HTTP tests run the real routes, persistence and controller with simulated providers.
The client tests cover version checking, CSRF, idempotent request payloads, polling,
cancellation and stale-composer protection. Real model smoke reports are local under
`runs/`; model checks are not human quality labels.

The Chrome integration work is to bundle/import the client, forward worker messages
to these endpoints, render the new result shape, and send explicit user edits. This
change does not switch the existing extension UI onto the new API or claim an installed
Chrome end-to-end test. No automatic tweet publishing is included.
