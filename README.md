# Mimicry

Turn a rough opinion into a clear draft, then match it to your own past writing
with less empty framing and repeated explanation. Browse the real X feed with
the Chrome side panel, or use the standalone local workspace. The source supplies meaning; your
references supply style only. Facts, requests, stance, certainty, and commitments
must stay intact. Reference anecdotes must not become invented personal experiences.

## Run the interface

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). From the project root:

```bash
uv sync --extra dev
# Only create .env if it does not already exist.
test -f .env || cp .env.example .env
# Add your API keys to .env before running the app.
uv run mimicry-web
```

Open http://127.0.0.1:2719. Add your writing samples in **My voice**, write your
opinion, then press **Rewrite & make it mine**. The example voices and draft are
original fictional samples, not human benchmark data. Editing inputs does not
call either model until you submit. Refreshing reconnects to the current
session's latest submitted draft and run. The standalone page does not autosave
unsubmitted changes; the Chrome side panel keeps them in session storage.
Restarting the server clears browser sessions; saved run files remain on disk.

The result separates **AI rewrite** from **In your voice**. Expand **See edits &
how the loop decided** to inspect the kept version's diff against your original.
Removed text is red and struck through; added text is green and underlined.
All rewrite attempts, including rejected ones, their diffs, ratings, meaning checks,
and complete run details remain available below the result.

Set `OPENAI_API_KEY` and `TYPESAFE_API_KEY` in the local `.env` file or process
environment. Keys stay server-side. References and draft text are sent to OpenAI
and TypeSafe; requests and responses are saved locally under ignored
`runs/mimicry/`. Optional `STYLE_WRITER_MODEL` and `STYLE_JUDGE_MODEL` override
the defaults `gpt-5.6-luna` and `jev-1.13.0`. The writer uses the OpenAI Responses API.

## Chrome writing assistant

The Manifest V3 extension lives in `chrome-extension/`. It requires Chrome 116+
and the local server at `http://127.0.0.1:2719`. The main interaction is now
inside X's own writing boxes: a small blue Mimicry button opens a suggestion
card beside your post or reply. The side panel remains available for **My voice**
and detailed review.

1. Open `chrome://extensions`, enable **Developer mode**, and choose **Load unpacked**.
   Select `chrome-extension/`.
2. Open X. Mimicry automatically appears beside post, reply, and quote writing
   boxes, including dialogs opened later. No toolbar click is needed. Your real
   **For You** and **Following** feeds remain available.
3. Write directly in X's post, reply, or quote box. Click the blue Mimicry button beside
   that box, then **My voice** to add your own samples or import tweets with OAuth.
4. Choose a writing mode and request a check. You can keep typing while it works.
   Models are called only when you request a check.
5. Click an underlined phrase to review an individual edit. **Accept** replaces
   that range; **Dismiss** keeps your words. Remaining marks follow accepted edits.
   **Undo last edit** restores the previous draft if you have not changed it.
   The full rewrite and loop decisions are available in collapsed details.
   Nothing is automatically posted.

The interface derives edit ranges from the accepted full rewrite. It does not
invent per-edit model explanations or attach the full rewrite's score to a partial
revision. Check again to evaluate a draft assembled from individual suggestions.
Underlines are positioned in a separate overlay, without adding markup to X's editor.

Each suggestion belongs to its original writing box and source text. If you edit
that text, close the composer, change pages, or start a different draft, Mimicry
will not overwrite it with an old result. Apply uses Chrome's native `insertText`
editing command instead of replacing X's editor DOM; it verifies the resulting
text. If the editor rejects the operation, the card asks you to check your draft again.
X's controlled-editor behavior must still be checked in the target Chrome profile.
The DOM selectors can change when X updates its app.

The card uses cool neutral surfaces and an indigo accent, distinct from X's blue.
It follows the X page's light or dark background. Its three modes execute different
provider sequences:

| Mode | Execution | Personal samples |
| --- | --- | --- |
| AI refine + Personalize | Luna clarity pass, then up to two TypeSafe-guided style passes | Required |
| Personalize only | Evaluate the original, then up to two style passes; no initial clarity pass | Required |
| AI refine only | One Luna clarity pass and one TypeSafe call with the two meaning checks; no style scoring | Not used |

All modes retain the original if the candidate fails meaning or tweet-length checks.
Refine-only meaning probabilities are not a claim that clarity improved. The UI
shows the actual accepted version, and the user decides whether to apply it.

For a reply dialog, the card offers a visible checkbox to include the parent post
as context. It never guesses context from other home-feed posts. The right-click
excerpt menu and the side panel's paste dialog are still available for standalone
writing in the studio.

The extension requests `activeTab` and `scripting` for an explicitly activated
page, `sidePanel`, `contextMenus`, and temporary `storage`. Its only persistent
host permission is loopback HTTP (`127.0.0.1`); network calls are restricted to
port 2719. It does not request permanent access to X. It attaches only to X's
post/reply editors, excluding direct messages, settings, and sign-in surfaces.
It does not trigger X's Post or Reply controls.

Code and styles are bundled locally. Credentials remain in the backend. The
content script can request only fixed writing operations through the extension
worker; it cannot supply arbitrary fetch URLs or read API keys, X tokens, the
CSRF token, or stored reference text. Jobs are scoped to the initiating tab and
document. Current drafts and voice samples use `chrome.storage.session`, clearing
on browser restart or **Sign out**. Submitted text and chosen context go to
OpenAI and TypeSafe through the local backend; local run artifacts remain on disk.

**Connect X** uses the existing OAuth flow in a normal tab; the panel reconnects
after the callback. Click **Reconnect** after restarting the server. The backend
allows the bundled extension's fixed public ID and still requires a session cookie
and CSRF token. The manifest's public `key` fixes the development ID and is not an
API credential. This is an unpacked development extension, not a Web Store release.

### Try the inline interaction locally

Open `http://127.0.0.1:2719/inline-demo` with the local server running. This labeled
playground uses the same suggestion card, diff renderer, and apply/undo adapter as
the extension. It has fictional samples and separate post/reply editors, calls the
live backend when requested, and cannot publish. It verifies the interaction in
an ordinary contenteditable editor, not X's React integration or extension cookies.

After changing shared UI files, rebuild, reload the extension in Chrome's extension
manager, then reload existing X tabs once. Subsequent page loads activate automatically:

```bash
uv run python tools/build_extension.py
node --test tests/test_extension.mjs
```

The builder copies only the shared HTML, JavaScript, and CSS and generates icons.
It never copies `.env`, run artifacts, or private notes.

## Run from the terminal

```bash
uv run mimicry --preset "Dry humor"
uv run mimicry --reference-file references.txt --draft-file ai-draft.txt
uv run mimicry --preset "Clear and practical" --max-revisions 1
uv run mimicry --reference-file my-tweets.txt --draft-file draft.txt --format tweet
```

## Tweets and X import

### Sign in with your own X account

In the X developer console, enable OAuth 2.0 for a **Web App / Confidential client**.
Register `http://127.0.0.1:2719/auth/x/callback` exactly. Put `X_CLIENT_ID`,
`X_CLIENT_SECRET`, and `X_REDIRECT_URI` in `.env`, then start `uv run mimicry-web`.
Use the `127.0.0.1` address consistently so the browser keeps the same session cookie.
The listening port comes from `X_REDIRECT_URI`; the server binds only to loopback.

Click **Connect X** and authorize the app on X. The server implements the official
[Authorization Code flow with PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/user-access-token):
random state, a S256 challenge, a single-use callback with a ten-minute timeout,
and confidential-client authentication during the token exchange. It requests
only `tweet.read users.read`. There is no publishing or direct-message integration.
App credentials and user access tokens never enter browser JSON, cookies, or run files.
An opaque HttpOnly, SameSite cookie identifies a server-side session; writes require
an origin check and a session CSRF token. Access logs are disabled to avoid logging
callback authorization codes.

After sign-in, Mimicry loads one page of up to 25 own tweets. The standalone
workspace also loads one page of the chronological home timeline. Each request can independently show API-credit,
permission, or rate-limit errors. Select your own writing samples, pick a feed post
with **Add my take**, and draft your response. Feed text is context; it does
not authorize inventing your opinions or experiences. The TypeSafe panel displays
the exact questions and criteria used by the engine, and each evaluated version
exposes its raw answers and probabilities.

Tokens and timeline caches live only in the server process and are isolated by
browser session. Tokens expire without requesting offline access; sign in again
when prompted. **Sign out** clears the local session and attempts X token revocation.
If X cannot confirm revocation, the interface says so and points to X Connected apps.
Signing out does not delete existing local run artifacts. This is a local demo,
not a hosted multi-user deployment: production requires HTTPS, durable secure
session storage, and deployment-specific limits.

### Tweet formatting

The interface defaults to **Single tweet**, with **General writing** still available.
Tweet mode uses the same meaning-preservation loop, with questions adapted for
short posts, casing, contractions, line breaks, and conversational tone. It does
not automatically add hooks, hashtags, emoji, or engagement bait.

The `twitter-text-parser` Python port checks standard 280-character eligibility
using weighted length, including CJK, combined emoji, NFC normalization, and URLs.
An invalid rewrite cannot be selected even if its model ratings improve. If no
eligible rewrite is accepted, the original stays intact and its length is shown.
The current mode is for one standard tweet; long posts and threads are not supported.
The parser requires `pkg_resources`, so the project pins a compatible setuptools.

### Legacy notebook importer

The earlier notebook remains available separately:

```bash
uv run marimo run notebooks/studio.py --host 127.0.0.1 --port 2720
```

It uses a public-tweet importer and restores local cached results. It does not
provide OAuth login or session isolation; use `mimicry-web` for the new workspace.

To import public posts from the official X API, put your app's Bearer Token in
the local `.env` file as `X_BEARER_TOKEN`. The form starts at `@jerrycxu`; you can
change the username. Click **Load my tweets**, then select examples you wrote
yourself and still want to emulate. You can edit the resulting reference text.
Selection is manual: an X account's authorship does not establish that a post was
written without AI. The interface shows how many tweets are selected and checks
the combined reference length before enabling **Rewrite in my voice**. Reference
writing needs 80–12,000 characters; several short tweets can be combined. Changing
the selection keeps the draft and output format intact. Validation errors do not
clear the previous result or trigger model calls.

One import makes a username lookup and a single timeline request for 10, 25, or
50 recent posts. It does not paginate automatically. Replies, reposts, quotes, and
duplicate text are excluded; full long-post text is preferred over its preview.
Normalized samples and their source links are saved under ignored
`runs/mimicry/x-imports/`. Refreshing restores the latest cached import without
another X request; select the desired samples again. Expanded quoted posts and authorization headers are
not saved. Importing alone does not send samples to the writing models; submitting
the rewrite form sends the selected reference text. The importer requires the
X app's access to these endpoints and applicable credits. It performs GET requests
only; this demo has no publishing integration.

Endpoint parameters follow the live [user posts reference](https://docs.x.com/x-api/users/get-posts),
including its current `post.fields` and `note_post` names. The normalizer also
understands `note_tweet` and `referenced_tweets` responses. See
[user lookup](https://docs.x.com/x-api/users/get-user-by-username) and
[character counting](https://docs.x.com/fundamentals/counting-characters).

## Feedback and selection

The sequence below describes the default **AI refine + Personalize** mode. With
two style passes it makes seven provider calls. **Personalize only** skips the
initial writer call and its candidate judgment (five calls). **AI refine only**
makes two calls and checks meaning and tweet length without scoring personal style.

1. Luna first improves expression using the immutable source and optional feed
   context, without the style references. TypeSafe evaluates both the original
   and this candidate. The same acceptance policy determines which is kept.
2. Luna rewrites the kept text using the actual ratings, the rubrics, and the two weakest
   style dimensions. The immutable source remains in every writer and judge request.
3. TypeSafe evaluates the rewrite. Code accepts or rejects it using the policy below.
4. If accepted, the next attempt starts from that rewrite with its new feedback.
   Rejected attempts and reasons are included in the next attempt's feedback.
   Stop after two style passes, provider failure, or a repeated draft. Keep the last accepted version;
   if none was accepted, keep the exact original input.

A complete two-style-pass run makes three writer calls and four TypeSafe calls,
excluding rate-limit retries. A one-style-pass CLI run makes two writer calls and
three TypeSafe calls. A repeated candidate stops before another judge call.
A provider failure preserves the original and any already accepted
rewrite; an unjudged attempt is retained for inspection but is not selected.

Four project-defined Score questions compare voice, rhythm, expression, and
structure against the actual references. A separate Score measures freedom from
filler: empty framing, redundant explanation, generic uplift, and unearned concluding
lessons. It does not reward brevity alone or ban expressive detail, metaphor, or
particular words. Scores run from 0 to 4 and are not probabilities of authorship.

Two Noul questions estimate whether substantive meaning is preserved and unsupported
factual or personal additions are absent. Both must reach 0.80 for a rewrite to be
eligible. Mean style and freedom from filler must each be at least as high as in
the current version; at least one must improve by 0.05. These are prototype
heuristics, not calibrated guarantees. Raw question definitions, distributions,
and model responses are retained with each run.

The questions are handwritten and fixed within a run. This demonstrates a bounded
editing feedback loop; it does not establish human preference or superiority over
a GPT evaluator. No model weights are trained, and no authorship detector is used.
The user's judgment remains the test of whether the writing sounds like them.

The design follows TypeSafe's [building guide](https://docs.typesafe.ai/concepts/how-to-build-with-system-one),
[Score](https://docs.typesafe.ai/primitives/score),
[Noul](https://docs.typesafe.ai/primitives/noul), and
[composite scoring](https://docs.typesafe.ai/patterns/composite-scoring) patterns.

## Development checks

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src notebooks tests
uv run python tools/build_extension.py
node --test tests/test_extension.mjs
uv run marimo check notebooks/studio.py
```

The tests use mocked provider responses and need no API keys. Local credentials,
imported tweets, writing samples, model responses, and private notes are ignored
by Git. Only the fictional samples in `src/mimicry/examples.py` ship with the app.
