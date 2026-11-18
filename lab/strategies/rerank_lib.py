"""rerank_lib: deterministic processing around the model call (investigator tag: rerank).

Everything here is code before or after the picker's Claude call. The picker itself is the baseline's: same two
searches, same prompt.md, same model, so every baseline Claude call is a cache hit and only the new steps cost.

LAB REPLAY NOTE. baseline.py puts `now: <UTC hour>` in the prompt, so its cached Claude calls only hit during the
hour they were made. NOW below pins the hour of the lead's baseline dev run (12:00 UTC); in the game use the clock.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

PROMPT = (Path(__file__).resolve().with_name("baseline_prompt.md")).read_text(encoding="utf-8").strip()
LANES = [("any_language", None), ("chinese_or_indonesian", ["zh", "id"])]
NOW = os.environ.get("LAB_NOW") or "2026-11-18T12:00:00Z"  # game: f"{datetime.now(timezone.utc):%Y-%m-%dT%H:00:00Z}"
BLANK = {"url": "", "summary": ""}


# --- before the model -------------------------------------------------------------------------------
def norm_url(url: str) -> str:
    """play.normalize_url, copied so a strategy does not import the game client."""
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    path = parts.path.rstrip("/")
    if path.endswith("/amp"):
        path = path[:-4]
    skip = {"fbclid", "gclid", "ref", "ref_src", "cmpid", "smid", "ocid", "src", "guccounter"}
    q = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in skip)
    return host + path + ("?" + urlencode(q) if q else "")


def search(clue: str, tools) -> dict:
    """The baseline's two searches: {lane: [articles]} with text cut to 900 characters."""
    requests = []
    for name, language in LANES:
        body = {"query": clue[:500], "count": 10, "time_range": "day", "highlight": {"enable": True, "max_tokens": 200}}
        if language:
            body["language"] = language
        requests.append({"name": name, "body": body})
    done = tools.search_many(requests)
    return {d["name"]: [{**a, "text": a["text"][:900]} for a in d["articles"]] for d in done}


def _ts(value: str | None) -> float | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def title_key(title: str | None) -> str:
    return re.sub(r"\W+", "", (title or "").lower())[:80]


def clean(results: dict, now: str = NOW, max_age_hours: float = 24, dedupe: bool = True, top: int = 0) -> tuple[dict, dict]:
    """Drop repeated articles (same normalized url, or same title: keep the earliest published), articles older
    than max_age_hours, and keep the first `top` per lane (0 = all). Returns (results, counts of what was dropped)."""
    now_ts = _ts(now) or datetime.now(timezone.utc).timestamp()
    dropped = {"old": 0, "dup_url": 0, "dup_title": 0, "cut": 0}
    seen_urls: set[str] = set()
    out = {}
    for lane, articles in results.items():
        kept: list[dict] = []
        by_title: dict[str, int] = {}
        for a in articles:
            t = _ts(a.get("published"))
            if max_age_hours and t is not None and now_ts - t > max_age_hours * 3600:
                dropped["old"] += 1
                continue
            if dedupe:
                u = norm_url(a.get("url") or "")
                if u in seen_urls:
                    dropped["dup_url"] += 1
                    continue
                seen_urls.add(u)
                k = title_key(a.get("title"))
                if k and k in by_title:
                    dropped["dup_title"] += 1
                    held = kept[by_title[k]]
                    if (t or 9e18) < (_ts(held.get("published")) or 9e18):  # same title: keep the first report, at the better rank
                        kept[by_title[k]] = a
                    continue
                if k:
                    by_title[k] = len(kept)
            kept.append(a)
        if top and len(kept) > top:
            dropped["cut"] += len(kept) - top
            kept = kept[:top]
        out[lane] = kept
    return out, dropped


def picker_prompt(clue: str, results: dict, now: str = NOW) -> str:
    return f"now: {now}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"


def pick(clue: str, results: dict, tools, model: str = "sonnet", effort: str = "low") -> dict:
    card = tools.ask(PROMPT, picker_prompt(clue, results), model=model, effort=effort)
    if not isinstance(card, dict):
        return dict(BLANK, summary="the model call failed")
    return {"url": str(card.get("url") or "").strip(), "summary": str(card.get("summary") or "").strip()}


# --- after the model ---------------------------------------------------------------------------------
def find(results: dict, url: str) -> tuple[str, int, dict] | None:
    """(lane, index, article) of the result with this url: exact first, then by normalized url."""
    for exact in (True, False):
        for lane, articles in results.items():
            for i, a in enumerate(articles):
                if (a.get("url") == url) if exact else (norm_url(a.get("url") or "") == norm_url(url)):
                    return lane, i, a
    return None


def guard(card: dict, results: dict) -> tuple[dict, str]:
    """The url must be one of the results (the game scores anything else as wrong): repair to the result's own
    spelling when it matches after normalizing, blank otherwise. Returns (card, what happened)."""
    url = card.get("url") or ""
    if not url:
        return card, "blank"
    hit = find(results, url)
    if hit is None:
        return dict(card, url="", summary="the model's link was not one of the search results"), "not_in_results"
    if hit[2]["url"] != url:
        return dict(card, url=hit[2]["url"]), "repaired"
    return card, "ok"


VERIFY_SYSTEM = (
    "You check one answer in a news game. A clue describes one news story without names; it paraphrases (\"a G7 economy\", "
    "\"an island currency\") and may round numbers. Some clues are fakes: a real story with exactly one fact changed (a number, "
    "a place, a nationality, a date or time of day, a direction such as ahead/behind or rose/fell, a cause, who did what). "
    "You get the clue and the article that was picked for it: title, publish time, text, and a one-sentence English summary.\n\n"
    "Go through every concrete fact in the clue: each number, amount, count, score, age, date, weekday, time of day, duration, "
    "place, nationality, kind of thing involved, cause or reason, direction, and what was said or done. For each, find what the article says.\n"
    "- A conflict: the article states something different for that fact (clue 34, article 33; clue a Danish pair, article a Japanese pair; "
    "clue behind schedule, article ahead of schedule; clue last week, article last month; clue 3.5 million, article 350,000).\n"
    "- Not a conflict: a paraphrase, a broader description that is still true of the article, a rounded number (nearly 5% for 4.9%), "
    "a unit conversion, or a fact the article simply does not mention.\n"
    "List the conflicts as 'clue: X / article: Y' (write 'none' when there are none), then give the verdict: 'conflict' when at least one "
    "fact clearly conflicts, otherwise 'ok'. When unsure, the verdict is 'ok'."
)
VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"conflicts": {"type": "string"}, "verdict": {"type": "string", "enum": ["ok", "conflict"]}},
    "required": ["conflicts", "verdict"],
    "additionalProperties": False,
}


def extract_text(url: str, tools, limit: int = 6000, timeout: int = 8) -> tuple[str, str]:
    """The article's own text from Octen Extract: (text, published) or ("", "") when the fetch fails."""
    raw = tools.octen("extract", {"urls": [url], "format": "text", "timeout": timeout})
    item = ((raw.get("data") or {}).get("results") or [{}])[0]
    if raw.get("code", 0) != 0 or item.get("status") != "success":
        return "", ""
    return (item.get("full_content") or "")[:limit], item.get("time_published") or ""


def verify(clue: str, card: dict, article: dict, tools, source: str = "highlight", model: str = "haiku", effort: str = "low") -> tuple[bool, str]:
    """A second, small Claude call: does the picked article contradict the clue on a concrete fact?

    source "highlight": the search result's own text (no extra fetch). "extract": the full article from Octen Extract
    (falls back to the highlight when the fetch fails). "summary": the picker's summary only. Returns (conflict, detail).
    """
    text = article.get("text") or ""
    if source == "extract":
        full, _ = extract_text(article["url"], tools)
        text = full or text
    if source == "summary":
        prompt = f"Clue: {clue}\n\nPicked article\nsummary: {card.get('summary')}\n"
    else:
        prompt = f"Clue: {clue}\n\nPicked article\ntitle: {article.get('title')}\npublished: {article.get('published')}\nsummary: {card.get('summary')}\ntext: {text}\n"
    out = tools.ask(VERIFY_SYSTEM, prompt, schema=VERIFY_SCHEMA, model=model, effort=effort, timeout=25)
    if not isinstance(out, dict):
        return False, "verify call failed"
    return out.get("verdict") == "conflict", str(out.get("conflicts") or "")[:300]


def answer_with(clue: str, tools, *, dedupe: bool = False, top: int = 0, verify_source: str = "", verify_model: str = "haiku",
                verify_effort: str = "low") -> dict:
    """Baseline picker with the deterministic steps switched on by keyword."""
    results = search(clue, tools)
    if dedupe or top:
        results, _ = clean(results, dedupe=dedupe, top=top)
    card, _ = guard(pick(clue, results, tools), results)
    if card["url"] and verify_source:
        _, _, article = find(results, card["url"])
        conflict, detail = verify(clue, card, article, tools, verify_source, verify_model, verify_effort)
        if conflict:
            return {"url": "", "summary": f"The closest story contradicts the clue: {detail}"[:300]}
    return card
