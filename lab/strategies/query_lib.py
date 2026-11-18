"""query_lib: shared pieces for the query-formulation study (tag: query). Not a strategy.

The baseline sends the raw clue as the query of both searches. Here: a haiku rewrite of the clue (real names
guessed, the story's country and local news language, a headline-style query in English and in that language),
a deterministic keyword-only version, and the body builders that keep cached baseline bodies byte-identical.
"""

import os
import re
from datetime import datetime, timezone

API_LANGS = ["ar", "de", "en", "es", "fr", "hi", "id", "it", "ja", "ko", "nl", "pl", "pt", "ru", "th", "tr", "vi", "zh"]


def now_stamp() -> str:
    """The `now:` line of the picker prompt. LAB_NOW freezes it so cached picker calls are reused in the lab."""
    return os.environ.get("LAB_NOW") or f"{datetime.now(timezone.utc):%Y-%m-%dT%H:00:00Z}"


def body(query: str, language: list | None = None, count: int = 10) -> dict:
    """Exactly the baseline's News Search body, with another query and language filter."""
    b = {"query": query[:500], "count": count, "time_range": "day", "highlight": {"enable": True, "max_tokens": 200}}
    if language:
        b["language"] = language
    return b


# --- the haiku rewrite ------------------------------------------------------------------------------
REWRITE_SYSTEM = """You turn a news-quiz clue into search queries for a news search engine. The clue describes one news story published in the last few hours, but it hides the names on purpose: people, organisations, places and headline words are replaced by descriptions ("a G7 economy", "a chipmaker", "a southern Chinese city", "a top court").

Return four fields.
country: the country or territory where the story happens, your best guess from the clue.
lang: the language that local news outlets there write in, one of: ar de en es fr hi id it ja ko nl pl pt ru th tr vi zh. Mainland China, Hong Kong, Macau, Taiwan, Singapore and Malaysia are zh. Indonesia is id. India is hi. A place whose main language is not in the list is en. A story with no clear place is en.
q_en: an English search query worded like the headline and first sentence a news outlet would publish for this story. Put the real name in place of a description wherever you can tell it (country, city, company, agency, office holder); leave the description when you cannot. Keep every number, date and concrete detail exactly as the clue gives it, and never correct or change a fact. Drop filler words. At most 30 words.
q_local: the same query written in the language `lang`, worded the way a local outlet would write its headline and first sentence, with the local names and official terms (traditional characters for Taiwan, Hong Kong and Macau, simplified otherwise). An empty string when lang is en."""

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "country": {"type": "string"},
        "lang": {"type": "string", "enum": API_LANGS},
        "q_en": {"type": "string"},
        "q_local": {"type": "string"},
    },
    "required": ["country", "lang", "q_en", "q_local"],
    "additionalProperties": False,
}


def rewrite(clue: str, tools, model: str = "haiku", effort: str = "low") -> dict | None:
    """One Claude call: {"country", "lang", "q_en", "q_local"}, or None when it failed."""
    return tools.ask(REWRITE_SYSTEM, f"Clue: {clue}", schema=REWRITE_SCHEMA, model=model, effort=effort, timeout=45)


# --- deterministic variants ----------------------------------------------------------------------------
_STOP = set(
    "a an the and or but of to in on at by for from with as that which who whom whose this these those it its their his her he she they them "
    "is are was were be been being has have had will would could should may might can do does did not no also than then so such while when where "
    "after before during over under into about against between through per both each one other another some any more most very said saying says "
    "say according told reported announced stated added noted amid among although though despite however whether if because since until upon via "
    "out up down off again there here what how why just only even still already".split()
)


def keywords(clue: str) -> str:
    """The clue without stop words and attribution filler: content words, numbers and names, in order."""
    words = re.findall(r"[^\s,;:()\"“”]+", clue)
    kept = [w.strip(".'’") for w in words if w.strip(".'’").lower() not in _STOP]
    return " ".join(w for w in kept if w)
