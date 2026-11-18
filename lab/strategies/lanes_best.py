"""lanes_best: the baseline, with the second search routed by where the clue says the story happens.

First search: any language (in practice English: 98% of its results are English, and adding language=["en"]
returns the same list). Second search, chosen by a free keyword rule on the clue (lanes_common.route):
  - the clue names a place whose press is in another language the API can filter on (Japan, Latin America,
    the Arab world, France, Turkey, India, ...) and no Chinese or Indonesian place: the wide lane, every API language
    except English. On dev this is 24 of 150 clues; it reaches the Japanese, Spanish, French, Arabic, Turkish and
    Portuguese stories that neither baseline search ever returns.
  - anything else (a Chinese or Indonesian place, or no place at all): Chinese or Indonesian, exactly the baseline.
Both searches still run at once, so the time per clue is the baseline's. The picker is the baseline's (prompt.md,
sonnet, low effort); routed clues get the same prompt with the second lane renamed and one sentence added to rule 3.

Set LAB_NOW=2026-11-18T12:00:00Z to reuse the baseline's cached Claude calls for the unrouted clues.
"""

import json
import os
from datetime import datetime, timezone

from strategies import baseline
from strategies import lanes_common as lc

WIDE_PROMPT = baseline.PROMPT.replace(
    "`any_language`, and `chinese_or_indonesian` (only articles in those languages)",
    "`any_language` (mostly English), and `local_languages` (only articles that are not in English, from the press of many countries)",
).replace(
    "These must come from `chinese_or_indonesian`; use another language only when no article there reports the story. For stories anywhere else, answer with an English article when one reports it.",
    "These must come from `local_languages`; use another language only when no article there reports the story. For stories anywhere else, answer with an English article when one reports it; "
    "when none does, an article from `local_languages` in the language of the country where the story happens (Japanese, Spanish, Arabic, French and so on) is a good answer, if it passes rule 1.",
)
assert WIDE_PROMPT != baseline.PROMPT and "local_languages` in the language" in WIDE_PROMPT, "prompt.md changed: update the two replacements"


def second_lane(clue: str) -> str:
    flags = lc.route(clue)
    return "wide" if flags and not any(f in ("zh", "id") for f in flags) else "zh+id"


def retrieve(clue: str, tools) -> dict:
    if second_lane(clue) == "zh+id":
        return baseline.retrieve(clue, tools)
    done = tools.search_many([{"name": "any_language", "body": lc.body(clue, "any")}, {"name": "local_languages", "body": lc.body(clue, "wide")}])
    return {d["name"]: [{**a, "text": a["text"][:900]} for a in d["articles"]] for d in done}


def answer(clue: str, tools) -> dict:
    results = retrieve(clue, tools)
    now = os.environ.get("LAB_NOW") or f"{datetime.now(timezone.utc):%Y-%m-%dT%H:00:00Z}"
    prompt = f"now: {now}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"
    system = WIDE_PROMPT if "local_languages" in results else baseline.PROMPT
    return tools.ask(system, prompt, model="sonnet", effort="low") or {"url": "", "summary": ""}
