"""Original fictional fixtures, with preference labels fixed before model evaluation."""

CONTEXT = {"language": "en", "purpose": "build_update"}
REFERENCES = """the demo is up. still checking the numbers.

spent today getting the tests to run on a clean machine. smaller problem than i thought.

removed the extra configuration screen. one less decision to explain.

the script takes two inputs and writes one file. that's enough for now."""
PREFERENCE = (
    "In a build update with a concrete problem, mistake or tradeoff, start with that "
    "friction. Put the fix, improvement or next step after it. State my actual experience; "
    "don't turn it into a lesson for the reader. Keep every substantive fact and qualification. "
    "Don't invent friction when there isn't any. If I explicitly ask to lead with an "
    "announcement, respect that intent."
)


def pair(id_, source, preferred, other, *, split="test", kind="style"):
    return {
        "id": id_,
        "source_text": source,
        "after": preferred,
        "before": other,
        "split": split,
        "kind": kind,
        "label_provenance": "synthetic_author_defined",
        "context": CONTEXT,
        "explanation": PREFERENCE,
        "state": {
            "source_text": source,
            "references": REFERENCES,
            "context": CONTEXT,
            "output_format": "tweet",
            "feed_context": "",
            "contrasts": [],
        },
    }


TRAIN = [
    pair(
        "train_1",
        "We made search twice as fast, but the index now needs 8GB. I am testing pruning.",
        "the index now needs 8GB. search is twice as fast, but i'm testing pruning.",
        "search is twice as fast. the index now needs 8GB, so i'm testing pruning.",
        split="train",
    ),
    pair(
        "train_2",
        "I fixed login after spending three hours looking in the wrong service.",
        "spent three hours looking in the wrong service. login is fixed now.",
        "login is fixed now. spent three hours looking in the wrong service.",
        split="train",
    ),
    pair(
        "train_3",
        "The demo runs on my laptop. It still crashes on a clean install. I will fix setup.",
        "the demo still crashes on a clean install. runs on my laptop. fixing setup next.",
        "the demo runs on my laptop. still crashes on a clean install. fixing setup next.",
        split="train",
    ),
    pair(
        "train_4",
        "I shipped CSV export after realizing I had tested it only with an empty table.",
        "realized i'd only tested export with an empty table. CSV export is shipped now.",
        "CSV export is shipped now. realized i'd only tested it with an empty table.",
        split="train",
    ),
]

TEST = [
    pair(
        "test_1",
        "The new model gained two points offline, but production latency doubled. I am "
        "keeping the baseline while testing compression.",
        "production latency doubled. the new model gained two points offline, but i'm "
        "keeping the baseline while testing compression.",
        "the new model gained two points offline. production latency doubled, so i'm "
        "keeping the baseline while testing compression.",
    ),
    pair(
        "test_2",
        "We got the dashboard loading in 400ms after deleting a widget that nobody used.",
        "nobody used that widget. deleted it; the dashboard now loads in 400ms.",
        "dashboard now loads in 400ms. deleted a widget nobody used.",
    ),
    pair(
        "test_3",
        "I fixed a duplicate payment bug. The retry worker was running twice.",
        "the retry worker was running twice. fixed the duplicate payment bug.",
        "fixed the duplicate payment bug. the retry worker was running twice.",
    ),
    pair(
        "test_4",
        "The app now works offline, but the first sync still takes 30 seconds. I am profiling it.",
        "first sync still takes 30 seconds. the app works offline now; i'm profiling the sync.",
        "the app works offline now. first sync still takes 30 seconds; i'm profiling it.",
    ),
    pair(
        "test_5",
        "I finished the migration after finding a timezone assumption in a five-year-old script.",
        "a five-year-old script assumed the wrong timezone. found it and finished the migration.",
        "migration finished. found a timezone assumption in a five-year-old script.",
    ),
    pair(
        "test_6",
        "We can serve twice as many requests, but memory use is up 40%. I am not rolling "
        "this out yet.",
        "memory use is up 40%. we can serve twice as many requests, but i'm not rolling "
        "this out yet.",
        "we can serve twice as many requests. memory use is up 40%, so i'm not rolling "
        "this out yet.",
    ),
    pair(
        "test_7",
        "After two failed deploys, I found a missing environment variable. The third "
        "deploy worked.",
        "two deploys failed because i missed an environment variable. added it; the third worked.",
        "third deploy worked after i added the missing environment variable. the first two "
        "had failed.",
    ),
    pair(
        "test_8",
        "I got the prototype into five users' hands, and all five got stuck at the same "
        "setup step. I am rewriting that step.",
        "all five users got stuck at the same setup step. the prototype's in their hands; "
        "i'm rewriting that step.",
        "got the prototype into five users' hands. all five got stuck at the same setup "
        "step; i'm rewriting it.",
    ),
    pair(
        "test_9",
        "The parser passes all 200 tests now, but I discovered the suite had no emoji "
        "cases. I am adding them.",
        "the suite had no emoji cases. parser passes all 200 tests now; i'm adding the "
        "missing cases.",
        "parser passes all 200 tests now. discovered the suite had no emoji cases; i'm "
        "adding them.",
    ),
    pair(
        "test_10",
        "I reduced the API response by half. Three clients still depend on the removed "
        "field, so I am keeping the old endpoint.",
        "three clients still need that field. halved the API response, but i'm keeping the "
        "old endpoint for them.",
        "halved the API response. three clients still need the removed field, so i'm "
        "keeping the old endpoint.",
    ),
    pair(
        "test_11",
        "The model finally trains on one GPU. The batch size is only two, so the run will "
        "take all night.",
        "batch size is only two, so this will take all night. the model finally trains on one GPU.",
        "the model finally trains on one GPU. batch size is only two, so the run will take "
        "all night.",
    ),
    pair(
        "test_12",
        "I published the tutorial, then noticed the first command fails on Windows. I have "
        "added a Windows-specific command.",
        "noticed the tutorial's first command fails on Windows after publishing it. added "
        "a Windows-specific command.",
        "published the tutorial and added a Windows-specific command after noticing the "
        "first command fails there.",
    ),
    pair(
        "exception_1",
        "The export button is live for all users. I have no problems to report.",
        "export button is live for everyone. no problems to report.",
        "export was a mess. finally got the button live for everyone.",
        kind="no_invented_friction",
    ),
    pair(
        "exception_2",
        "We finished the documentation refresh. Please just announce it; there was no incident.",
        "documentation refresh is done. no incident, just the announcement.",
        "the docs were confusing everyone. finished the refresh.",
        kind="no_invented_friction",
    ),
    pair(
        "exception_3",
        "Lead with the launch announcement: the beta is open. Then mention that Android "
        "support comes next month.",
        "the beta is open. Android support comes next month.",
        "Android support comes next month. the beta is open.",
        kind="explicit_intent",
    ),
    pair(
        "exception_4",
        "Start by announcing that we shipped version 2. Then note that old plugins need an update.",
        "we shipped version 2. old plugins need an update.",
        "old plugins need an update. we shipped version 2.",
        kind="explicit_intent",
    ),
    pair(
        "meaning_1",
        "The cache cut median latency by 30%, but p99 got 10% worse. I am investigating "
        "before rollout.",
        "p99 got 10% worse. cache cut median latency by 30%; i'm investigating before rollout.",
        "p99 got 10% worse. cache cut median latency by 30%; rolling it out now.",
        kind="meaning",
    ),
    pair(
        "meaning_2",
        "I think the timeout is caused by the connection pool, but I have not confirmed "
        "it. I will test it tomorrow.",
        "timeouts might be the connection pool. not confirmed; testing tomorrow.",
        "timeouts are caused by the connection pool. confirmed it; testing tomorrow.",
        kind="meaning",
    ),
]
