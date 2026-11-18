"""params_lib: helpers for the Search-API-parameter study (tag: params). Not a strategy."""

import json
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(LAB), str(LAB.parent)]

import octen_cached  # noqa: E402
import play  # noqa: E402


def clues(split: str = "mini") -> list[dict]:
    cs = json.loads((LAB / "clues.json").read_text(encoding="utf-8"))
    if split == "mini":
        return [c for c in cs if c.get("mini")]
    return [c for c in cs if c["split"] == split]


def base_body(clue: str, language=None, **over) -> dict:
    body = {"query": clue[:500], "count": 10, "time_range": "day", "highlight": {"enable": True, "max_tokens": 200}}
    if language:
        body["language"] = language
    for k, v in over.items():
        if v is None:
            body.pop(k, None)
        else:
            body[k] = v
    return body


def cached(body: dict, api: str = "news-search"):
    p = octen_cached._key(api, body)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def targets(c: dict) -> set[str]:
    """URLs that count as 'the story found': the key articles plus the source article."""
    urls = {play.normalize_url(a["url"]) for a in c["key"]["articles"]}
    urls.add(play.normalize_url(c["source"]["url"]))
    return urls


def rank_of(c: dict, results: list[dict]) -> int | None:
    t = targets(c)
    for i, a in enumerate(results, start=1):
        if a.get("url") and play.normalize_url(a["url"]) in t:
            return i
    return None
