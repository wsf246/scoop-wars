"""Pick the stories to write clues from, and look up each one's lookalikes.

    uv run python lab/select_stories.py

Reads lab/stories.json. For every picked story it runs one search with the story's own headline, so the clue
writer can see which other articles report the same event (the answer key) and which are similar but different
events (traps). Writes lab/slices/slice-1.json ... slice-5.json, one per clue writer.
"""

import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_DIR))
sys.path.insert(0, str(LAB_DIR.parent))

import octen_cached  # noqa: E402
import play  # noqa: E402

FRESH_SINCE = "2026-11-17T20:00"  # clues are about the last few hours
JUNK = re.compile(
    r"horoscope|zodiak|ramalan|jadwal|prakiraan|harga emas|togel|lottery|live blog|live updates|how to watch|"
    r"星座|运势|運勢|开奖|開獎|直播|懶人包|一文看|promo|diskon|resep|recipe|quiz|crossword|wordle|podcast|opinion|editorial",
    re.I,
)
# stories per language for each writer; about 12% more than the clues asked for, so junk can be skipped
SLICES = [{"en": 112}, {"zh": 112}, {"zh": 88, "en": 24}, {"id": 112}, {"id": 53, "other": 45, "en": 14}]


def lookalikes(story: dict) -> list[dict]:
    body = {"query": story["title"][:300], "count": 10, "time_range": "week", "language": [story["language"]], "highlight": {"enable": False}, "subjects": {"enable": False}}
    results = (octen_cached.search(body).get("data") or {}).get("results") or []
    own = play.normalize_url(story["url"])
    return [
        {"url": a.get("url"), "title": a.get("title"), "time_published": a.get("time_published")}
        for a in results if a.get("url") and play.normalize_url(a["url"]) != own
    ][:8]


def main() -> None:
    random.seed(20261118)
    stories = [s for s in json.loads((LAB_DIR / "stories.json").read_text(encoding="utf-8")) if not JUNK.search(s["title"])]
    pools: dict[str, list[dict]] = {}
    for s in stories:
        pools.setdefault(s["language"] if s["language"] in ("en", "zh", "id") else "other", []).append(s)
    for pool in pools.values():
        random.shuffle(pool)
        pool.sort(key=lambda s: (s.get("time_published") or "") < FRESH_SINCE)  # fresh stories first, random within
    slices = []
    for wanted in SLICES:
        picked = []
        for lang, n in wanted.items():
            picked += [pools[lang].pop(0) for _ in range(min(n, len(pools[lang])))]
        slices.append(picked)
    everything = [s for picked in slices for s in picked]
    with ThreadPoolExecutor(6) as ex:
        for s, similar in zip(everything, ex.map(lookalikes, everything)):
            s["similar_articles"] = similar
    out = LAB_DIR / "slices"
    out.mkdir(exist_ok=True)
    for i, picked in enumerate(slices, start=1):
        rows = [
            {
                "sid": s["sid"], "language": s["language"], "title": s["title"], "url": s["url"], "time_published": s["time_published"],
                "text": s["text"][:700], "similar_articles": s["similar_articles"],
            }
            for s in picked
        ]
        (out / f"slice-{i}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        fresh = sum((s.get("time_published") or "") >= FRESH_SINCE for s in picked)
        print(f"slice-{i}: {len(rows)} stories ({fresh} fresh), languages {sorted({s['language'] for s in picked})}")
    print("live calls so far:", octen_cached.spend()["live_calls"])


if __name__ == "__main__":
    main()
