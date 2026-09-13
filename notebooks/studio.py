import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="Mimicry")


@app.cell
def _():
    import html
    import json
    import sys
    from pathlib import Path

    import marimo as mo

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root / "src") not in sys.path:
        sys.path.insert(0, str(project_root / "src"))
    from mimicry.diff import edit_difference
    from mimicry.engine import STYLE_DIMENSIONS, input_errors, rewrite, settings
    from mimicry.examples import DRAFT, PRESETS
    from mimicry.tweets import import_tweets

    return (
        DRAFT, PRESETS, STYLE_DIMENSIONS, edit_difference, html, json,
        import_tweets, input_errors, mo, project_root, rewrite, settings,
    )


@app.cell
def _(json, project_root):
    saved_result = None
    _paths = (project_root / "runs" / "mimicry").glob("*/result.json")
    for _path in sorted(_paths, reverse=True):
        try:
            _run = json.loads(_path.read_text())
            if _run.get("selected") and _run.get("status") in ("complete", "partial"):
                saved_result = _run
                break
        except (OSError, ValueError):
            continue
    return (saved_result,)


@app.cell
def _(json, project_root):
    saved_tweets = None
    for _path in (project_root / "runs" / "mimicry" / "x-imports").glob("*.json"):
        try:
            _import = json.loads(_path.read_text())
            if (isinstance(_import, dict) and isinstance(_import.get("posts"), list)
                    and _import.get("username")):
                if saved_tweets is None or _import.get("fetched_at", "") > saved_tweets.get(
                    "fetched_at", ""
                ):
                    saved_tweets = _import
        except (OSError, ValueError):
            continue
    return (saved_tweets,)


@app.cell
def _(mo):
    mo.Html("""
    <style>
    :root { --heist-ink: #18251e; --heist-muted: #59625b; --heist-paper: #fffdf7; }
    .heist-intro { padding: 28px 0 16px; }
    .heist-eyebrow { font: 600 12px/1.5 system-ui; letter-spacing: .15em;
                     text-transform: uppercase; color: var(--heist-muted); }
    .heist-title { font: 500 clamp(38px, 7vw, 64px)/1.08 Georgia, serif;
                   color: var(--heist-ink); margin: 12px 0 18px; }
    .heist-description { font: 17px/1.6 system-ui; color: var(--heist-muted); max-width: 580px; }
    .heist-writing { white-space: pre-wrap; overflow-wrap: anywhere;
        font: 19px/1.75 Georgia, serif; background: var(--heist-paper);
        color: var(--heist-ink); border: 1px solid #c4c8be; border-radius: 12px;
        padding: clamp(18px, 4vw, 32px); }
    .heist-removed { background: #ffe4e3; color: #8f2024;
        text-decoration: line-through; text-decoration-thickness: 1.5px; }
    .heist-added { background: #dcf3e4; color: #165c36;
        text-decoration: underline; text-decoration-thickness: 1.5px;
        text-underline-offset: 3px; }
    .heist-diff del, .heist-diff ins { border-radius: 3px; padding: 0 .08em; }
    .heist-diff del + ins { margin-left: .18em; }
    .heist-diff-key { display: flex; flex-wrap: wrap; gap: 16px;
        font: 14px/1.6 system-ui; margin: 0 0 12px; }
    .heist-diff-key span { padding: 2px 7px; border-radius: 3px; }
    </style>
    <div class="heist-intro">
      <div class="heist-eyebrow">A small writing experiment</div>
      <h1 class="heist-title">Your meaning.<br>Your voice. Less filler.</h1>
      <p class="heist-description">Bring your past writing and an AI draft that says what
      you mean. Make it sound like you, without the empty framing or repeated explanations.</p>
    </div>
    """)
    return


@app.cell
def _(mo, saved_tweets):
    x_import_form = mo.ui.batch(
        mo.md("""
        **Import your writing from X**

        {username} {limit}

        Load recent public tweets, then select the ones you wrote yourself and still like.
        """),
        {
            "username": mo.ui.text(
                value=saved_tweets["username"] if saved_tweets else "jerrycxu",
                label="Your X username",
            ),
            "limit": mo.ui.dropdown(options=[10, 25, 50], value=25, label="Recent posts"),
        },
    ).form(submit_button_label="Load my tweets", show_clear_button=False)
    mo.output.append(x_import_form)
    return (x_import_form,)


@app.cell
async def _(import_tweets, mo, project_root, saved_tweets, settings, x_import_form):
    imported_tweets = saved_tweets
    if x_import_form.value:
        try:
            with mo.status.spinner(title="Loading your public tweets…"):
                imported_tweets = await import_tweets(
                    x_import_form.value["username"], settings().get("X_BEARER_TOKEN", ""),
                    limit=x_import_form.value["limit"],
                    output_root=project_root / "runs" / "mimicry" / "x-imports",
                )
        except (ValueError, RuntimeError) as _error:
            mo.output.append(mo.callout(str(_error), kind="warn"))
    elif saved_tweets:
        mo.output.append(mo.md(
            "*Restored your last X import from the local cache. "
            "Load my tweets fetches a fresh page.*"
        ))
    elif not settings().get("X_BEARER_TOKEN"):
        mo.output.append(mo.md(
            "*To connect X, add `X_BEARER_TOKEN` to your local `.env` file. "
            "You can also paste tweets into the writing samples below.*"
        ))
    return (imported_tweets,)


@app.cell
def _(imported_tweets, mo):
    tweet_samples = None
    use_tweets = None
    if imported_tweets:
        _posts = imported_tweets["posts"]
        if _posts:
            tweet_samples = mo.ui.table(
                _posts, selection="multi", initial_selection=[],
                wrapped_columns=["text"], show_download=False, page_size=5,
                label="Choose your own writing samples",
            )
            use_tweets = mo.ui.checkbox(value=True, label="Use selected tweets as my reference")
            mo.output.append(mo.vstack([
                mo.md(f"Loaded **{len(_posts)}** standalone samples from "
                      f"**@{imported_tweets['username']}**. "
                      "Select the checkboxes beside the samples you want to use."),
                tweet_samples, use_tweets,
            ]))
        else:
            mo.output.append(mo.md(
                "No standalone text remained after filtering this page. "
                "Try a larger recent-post count or paste your samples below."
            ))
    return tweet_samples, use_tweets


@app.cell
def _(PRESETS, mo, saved_result):
    initial_preset = "Dry humor"
    if saved_result:
        initial_preset = next(
            (_name for _name, _text in PRESETS.items() if _text == saved_result["references"]),
            "Your writing",
        )
    preset = mo.ui.dropdown(
        options=[*PRESETS, "Your writing"], value=initial_preset,
        label="Start with a sample voice",
    )
    mo.vstack([
        preset,
        mo.md("*These are original fictional examples. Replace them with your own writing below.*"),
    ])
    return initial_preset, preset


@app.cell
def _(PRESETS, initial_preset, mo, preset, saved_result, tweet_samples, use_tweets):
    _reference = (
        saved_result["references"] if saved_result and preset.value == initial_preset
        else PRESETS.get(preset.value, "")
    )
    if tweet_samples is not None and use_tweets.value:
        _reference = "\n\n---\n\n".join(_row["text"] for _row in tweet_samples.value)
        _count = len(tweet_samples.value)
        _noun = "tweet" if _count == 1 else "tweets"
        mo.output.append(mo.md(f"**{_count} {_noun} selected** for your reference."))
    reference_input = mo.ui.text_area(
        value=_reference, label="Your own writing samples (80–12,000 characters)",
        rows=9, max_length=12000, full_width=True,
    )
    mo.output.append(mo.vstack([
        mo.md("""
        **1 · Your past writing**

        Select tweets above or paste your own writing below. Short samples can be
        combined to reach 80 characters. These guide the voice, not the facts.
        """),
        reference_input,
    ]))
    return (reference_input,)


@app.cell
def _(html, input_errors, mo, reference_input, tweet_samples, use_tweets):
    reference_error = input_errors(reference_input.value, "Valid draft").get("references")
    if reference_error:
        if (not reference_input.value.strip() and tweet_samples is not None
                and use_tweets.value and not len(tweet_samples.value)):
            reference_error = (
                "No tweets selected. Select the checkboxes beside your tweets above, "
                "or paste your own writing here. Combine at least 80 characters."
            )
        _message = reference_error
    else:
        _message = f"Reference ready · {len(reference_input.value.strip()):,} characters."
    mo.Html('<p role="status">' + html.escape(_message) + '</p>')
    return (reference_error,)


@app.cell
def _(DRAFT, mo, saved_result):
    _source_text = DRAFT
    if saved_result:
        _source_text = saved_result.get("source_text") or saved_result["versions"][
            saved_result["selected"]
        ]["text"]
    draft_input = mo.ui.text_area(
        value=_source_text, label="Existing AI draft",
        rows=7, max_length=12000, full_width=True,
    )
    format_input = mo.ui.dropdown(
        options={"Single tweet · 280 weighted characters": "tweet", "General writing": "text"},
        value="Single tweet · 280 weighted characters", label="Output format",
    )
    mo.vstack([
        mo.md("""
        **2 · AI draft to rewrite**

        Paste the draft that already says what you mean. Its meaning stays the anchor.
        """),
        draft_input, format_input,
    ])
    return draft_input, format_input


@app.cell
def _(draft_input, html, input_errors, mo, reference_error, reference_input):
    _draft_error = input_errors(reference_input.value, draft_input.value).get("source_text")
    if _draft_error:
        mo.output.append(mo.Html('<p role="status">' + html.escape(_draft_error) + '</p>'))
    _error = reference_error or _draft_error
    rewrite_button = mo.ui.run_button(
        label="Rewrite in my voice", disabled=bool(_error),
        tooltip=_error or "Rewrite using the reference and draft above",
    )
    mo.output.append(rewrite_button)
    return (rewrite_button,)


@app.cell
def _(mo, settings):
    _config = settings()
    _missing = [k for k in ("OPENAI_API_KEY", "TYPESAFE_API_KEY") if not _config.get(k)]
    mo.md(
        "**Setup needed:** Add " + ", ".join(_missing) + " to the project `.env` file."
        if _missing else
        "<small>Models connected · Up to two rewrites · Original and edits saved</small>"
    )
    return


@app.cell
def _(mo, saved_result):
    get_result, set_result = mo.state((saved_result, True))
    return get_result, set_result


@app.cell
async def _(draft_input, format_input, mo, reference_input, rewrite, rewrite_button, set_result):
    if rewrite_button.value:
        try:
            with mo.status.spinner(title="Reading your draft…") as _spinner:
                _result = await rewrite(
                    reference_input.value, draft_input.value,
                    output_format=format_input.value,
                    progress=lambda message: _spinner.update(title=message),
                )
                set_result((_result, False))
        except (ValueError, RuntimeError) as _error:
            mo.output.append(mo.callout(str(_error), kind="warn"))
    return


@app.cell
def _(get_result):
    result, showing_saved = get_result()
    return result, showing_saved


@app.cell
def _(STYLE_DIMENSIONS, edit_difference, html, json, mo, result, showing_saved):
    mo.stop(result is None)
    _parts = []
    _is_rewrite = result.get("mode") == "rewrite"
    def _version_label(name):
        return name.replace("_", " ").title()

    if showing_saved:
        _notice = "Last saved rewrite." if _is_rewrite else "Previous generation run."
        _parts.append(mo.md(f"*{_notice} Click Rewrite in my voice to use the form above.*"))
    if result.get("error"):
        _parts.append(mo.callout(result["error"], kind="warn"))
    if result["selected"]:
        _selected = result["versions"][result["selected"]]
        _title = "Your rewrite" if _is_rewrite else "Your writing"
        if result["selected"] == "original":
            _title = "Original kept"
        if result["status"] != "complete":
            _title += " · incomplete run"
        _parts.extend([
            mo.md(f"## {_title}"),
            mo.Html('<div class="heist-writing">' + html.escape(_selected["text"]) + '</div>'),
            mo.download(
                _selected["text"].encode(), filename="my-writing.txt", label="Download writing"
            ),
            mo.md(result.get("decision", "")),
        ])
        if "tweet_check" in _selected:
            _check = _selected["tweet_check"]
            _parts.append(mo.md(f"**{_check['weighted_length']} / 280** weighted characters"))
            if not _check["valid"]:
                _parts.append(mo.callout(
                    "This kept version does not fit a standard tweet. No text was truncated.",
                    kind="warn",
                ))
        if _is_rewrite:
            _parts.append(mo.md("Kept version: **" + _version_label(result["selected"]) + "**"))
            if _selected.get("judgment", {}).get("style_mean", 4) < 2:
                _parts.append(mo.md(
                    "*The style ratings are still low. A cleaner draft can still sound unlike "
                    "you; references in a similar format may be more useful.*"
                ))
        if result["selected"] != "original" and not _selected.get(
            "judgment", {}
        ).get("content_check_passed", False):
            _parts.append(mo.callout(
                "Review this draft against your source: its content checks did not pass or finish.",
                kind="warn",
            ))
    if _is_rewrite or ("draft" in result["versions"] and "revision" in result["versions"]):
        _before = result["source_text"] if _is_rewrite else result["versions"]["draft"]["text"]
        _after = (
            result["versions"][result["selected"]]["text"] if _is_rewrite
            else result["versions"]["revision"]["text"]
        )
        _parts.extend([
            mo.md("### Edit differences"),
            mo.md("Your AI draft → Kept version" if _is_rewrite else "First draft → Revision"),
            mo.Html(
                '<div class="heist-diff-key"><span class="heist-removed">Removed</span>'
                '<span class="heist-added">Added</span>'
                '<span>Unchanged text stays plain</span></div>'
            ),
            mo.md("The original is unchanged. Rewrite attempts are available below.")
            if _before == _after
            else mo.Html(edit_difference(_before, _after)),
        ])
    _rows = []
    _dimensions = {k: v[0] for k, v in STYLE_DIMENSIONS.items()}
    if _is_rewrite:
        _dimensions["economy"] = "Freedom from filler"
    for _key, _label in _dimensions.items():
        _row = {"Dimension": _label}
        for _version, _data in result["versions"].items():
            _judgment = _data.get("judgment")
            if _judgment:
                _score = _judgment["economy"] if _key == "economy" else _judgment["style"][_key]
                _row[_version_label(_version) + " / 4"] = round(_score, 2)
        _rows.append(_row)
    if any("judgment" in _v for _v in result["versions"].values()):
        _parts.extend([
            mo.md("### TypeSafe ratings"),
            mo.ui.table(_rows, selection=None, show_download=False),
            mo.md("*Higher is better. These are model judgments, not proof of your preference.*"),
        ])
    if _is_rewrite and result.get("steps"):
        _parts.extend([
            mo.md("### Rewrite loop"),
            mo.ui.table([
                {"Attempt": _version_label(s["version"]),
                 "Focus": ", ".join(_dimensions[k] for k in s["focus_dimensions"]),
                 "Outcome": "Kept" if s["accepted"] else "Rejected",
                 "Reason": s["reason"]}
                for s in result["steps"]
            ], selection=None, show_download=False),
            mo.md(result.get("stop_reason", "")),
        ])
    _details = {
        _version_label(_name): mo.Html(
            '<div class="heist-writing">' + html.escape(_value["text"]) + '</div>'
        ) for _name, _value in result["versions"].items()
    }
    if _is_rewrite:
        for _name, _value in result["versions"].items():
            if _name != "original":
                _details[_version_label(_name) + " · changes from original"] = mo.Html(
                    edit_difference(result["source_text"], _value["text"])
                )
        _checks = []
        for _key, _label in (
            ("facts_preserved", "Meaning preserved"),
            ("no_invented_facts", "No unsupported additions"),
        ):
            _row = {"Check · P(yes)": _label}
            for _name, _value in result["versions"].items():
                if "judgment" in _value:
                    _row[_version_label(_name)] = _value["judgment"][_key]
            _checks.append(_row)
        _details["Meaning checks"] = mo.vstack([
            mo.ui.table(_checks, selection=None, show_download=False),
            mo.md("Both checks must reach 0.80 to accept a rewrite. This is a prototype threshold, "
                  "not a guarantee of correctness. The original remains the source of meaning."),
        ])
    _details["Inputs and full results"] = mo.Html(
        '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">'
        + html.escape(json.dumps(result, ensure_ascii=False, indent=2)) + '</pre>'
    )
    _parts.extend([
        mo.accordion(_details),
        mo.download(json.dumps(result, ensure_ascii=False, indent=2).encode(),
                    filename="mimicry-run.json", label="Download run details"),
    ])
    mo.vstack(_parts, gap=1.5)
    return


if __name__ == "__main__":
    app.run()
