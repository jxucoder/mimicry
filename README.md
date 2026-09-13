# Mimicry

Rewrite an existing AI draft so it sounds like your own past writing, with less
empty framing and repeated explanation. The source draft supplies meaning; your
references supply style only. Facts, requests, stance, certainty, and commitments
must stay intact. Reference anecdotes must not become invented personal experiences.

## Run the interface

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). From the project root:

```bash
uv sync --extra dev
cp .env.example .env
# Add your API keys to .env before running the app.
uv run marimo run notebooks/studio.py --host 127.0.0.1 --port 2719
```

Open http://127.0.0.1:2719. Paste your past writing and the AI draft you want to edit,
then press **Rewrite in my voice**. The example voices and draft are original
fictional samples, not human benchmark data. Editing inputs does not call either
API until **Rewrite in my voice** is clicked. Refreshing restores the last saved result without
new API calls, including results from the earlier generation demo.

The result shows the kept version and its diff against your original AI draft.
Removed text is red and struck through; added text is green and underlined.
All rewrite attempts, including rejected ones, their diffs, ratings, meaning checks,
and complete run details remain available below the result.

Set `OPENAI_API_KEY` and `TYPESAFE_API_KEY` in the local `.env` file or process
environment. Keys stay server-side. References and draft text are sent to OpenAI
and TypeSafe; requests and responses are saved locally under ignored
`runs/mimicry/`. Optional `STYLE_WRITER_MODEL` and `STYLE_JUDGE_MODEL` override
the defaults `gpt-5.6-luna` and `jev-1.13.0`. The writer uses the OpenAI Responses API.

## Run from the terminal

```bash
uv run mimicry --preset "Dry humor"
uv run mimicry --reference-file references.txt --draft-file ai-draft.txt
uv run mimicry --preset "Clear and practical" --max-revisions 1
uv run mimicry --reference-file my-tweets.txt --draft-file draft.txt --format tweet
```

## Tweets and X import

The interface defaults to **Single tweet**, with **General writing** still available.
Tweet mode uses the same meaning-preservation loop, with questions adapted for
short posts, casing, contractions, line breaks, and conversational tone. It does
not automatically add hooks, hashtags, emoji, or engagement bait.

The `twitter-text-parser` Python port checks standard 280-character eligibility
using weighted length, including CJK, combined emoji, NFC normalization, and URLs.
An invalid rewrite cannot be selected even if its model ratings improve. If no
eligible rewrite is accepted, the original stays intact and its length is shown.
The current mode is for one standard tweet; long posts and threads are not supported.
The parser requires `pkg_resources`, so the style extra pins a compatible setuptools.

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

1. TypeSafe evaluates the supplied original against the writing references.
2. GPT rewrites that text using the actual ratings, the rubrics, and the two weakest
   style dimensions. The immutable source remains in every writer and judge request.
3. TypeSafe evaluates the rewrite. Code accepts or rejects it using the policy below.
4. If accepted, the next attempt starts from that rewrite with its new feedback.
   Stop after two attempts or the first rejection. Keep the last accepted version;
   if none was accepted, keep the exact original input.

A complete two-attempt run makes two writer calls and three TypeSafe calls,
excluding rate-limit retries. A one-attempt run makes one writer call and two
TypeSafe calls. A provider failure preserves the original and any already accepted
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
uv run marimo check notebooks/studio.py
```

The tests use mocked provider responses and need no API keys. Local credentials,
imported tweets, writing samples, model responses, and private notes are ignored
by Git. Only the fictional samples in `src/mimicry/examples.py` ship with the app.
