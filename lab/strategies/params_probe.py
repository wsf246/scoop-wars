"""params_probe: run named request variants for a list of clue ids and store what came back (tag: params).

    LAB_TAG=params LAB_TAG_MAX=450 LAB_QPS=2 uv run python lab/strategies/params_probe.py <variant,variant> <split> [ids,comma or 'miss' or 'all' or 'nN' for first N]

Not a strategy: an experiment driver that calls octen_cached.search (cached, counted against the tag).
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import params_lib as L  # noqa: E402

NON_EN = ["ar", "de", "es", "fr", "hi", "id", "it", "ja", "ko", "nl", "pl", "pt", "ru", "th", "tr", "vi", "zh"]
OTHERS = [x for x in NON_EN if x not in ("zh", "id")]
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)  # fixed, so request bodies stay cacheable


def iso(hours_back: float) -> str:
    return (NOW - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")


def relevant_language(c: dict):
    """The baseline lane in which this clue's story should be found."""
    return ["zh", "id"] if c["language"] in ("zh", "id") else None


def rel(c: dict, **over) -> dict:
    """The lane of the new design in which this clue's story should be found: zh/id, or the 16 other languages."""
    lang = ["zh", "id"] if c["language"] in ("zh", "id") else ["en"] + OTHERS
    return L.base_body(c["text"], lang, subjects={"enable": False}, **over)


# variant -> function(clue dict) -> body
VARIANTS = {
    "base": lambda c: L.base_body(c["text"], relevant_language(c)),
    "baseA": lambda c: L.base_body(c["text"]),
    "baseB": lambda c: L.base_body(c["text"], ["zh", "id"]),
    "b17_c20": lambda c: L.base_body(c["text"], NON_EN, count=20),
    "b17_c10": lambda c: L.base_body(c["text"], NON_EN),
    "o15_c10": lambda c: L.base_body(c["text"], OTHERS),
    "a16": lambda c: L.base_body(c["text"], ["en"] + OTHERS),
    "all18": lambda c: L.base_body(c["text"], ["en"] + NON_EN),
    "o15_c20": lambda c: L.base_body(c["text"], OTHERS, count=20),
    "a16_nosubj": lambda c: L.base_body(c["text"], ["en"] + OTHERS, subjects={"enable": False}),
    "rel": lambda c: rel(c),
    "rel_c50": lambda c: rel(c, count=50),
    "rel_st36": lambda c: rel(c, time_range=None, start_time=iso(36)),
    "rel_st12": lambda c: rel(c, time_range=None, start_time=iso(12)),
    "rel_week": lambda c: rel(c, time_range="week"),
    "rel_crawl": lambda c: rel(c, time_basis="crawled"),
    "rel_pub": lambda c: rel(c, time_basis="published"),
    "rel_hl100": lambda c: rel(c, highlight={"enable": True, "max_tokens": 100}),
    "rel_hl400": lambda c: rel(c, highlight={"enable": True, "max_tokens": 400}),
    "rel_full": lambda c: rel(c, full_content={"enable": True, "max_tokens": 500}),
    "nosubj": lambda c: L.base_body(c["text"], relevant_language(c), subjects={"enable": False}),
    "c50": lambda c: L.base_body(c["text"], relevant_language(c), count=50),
    "c50_nosubj": lambda c: L.base_body(c["text"], relevant_language(c), count=50, subjects={"enable": False}),
    "hl100": lambda c: L.base_body(c["text"], relevant_language(c), highlight={"enable": True, "max_tokens": 100}),
    "hl400": lambda c: L.base_body(c["text"], relevant_language(c), highlight={"enable": True, "max_tokens": 400}),
    "full400": lambda c: L.base_body(c["text"], relevant_language(c), full_content={"enable": True, "max_tokens": 400}),
    "full1000_nohl": lambda c: L.base_body(c["text"], relevant_language(c), full_content={"enable": True, "max_tokens": 1000}, highlight={"enable": False}),
    "tb_pub": lambda c: L.base_body(c["text"], relevant_language(c), time_basis="published"),
    "tb_crawl": lambda c: L.base_body(c["text"], relevant_language(c), time_basis="crawled"),
    "st36": lambda c: L.base_body(c["text"], relevant_language(c), time_range=None, start_time=iso(36)),
    "st12": lambda c: L.base_body(c["text"], relevant_language(c), time_range=None, start_time=iso(12)),
    "st24": lambda c: L.base_body(c["text"], relevant_language(c), time_range=None, start_time=iso(24)),
    "week": lambda c: L.base_body(c["text"], relevant_language(c), time_range="week"),
    "notime": lambda c: L.base_body(c["text"], relevant_language(c), time_range=None),
    "subj5": lambda c: L.base_body(c["text"], relevant_language(c), subjects={"enable": True, "count": 5, "max_sub_news": 10}),
}


def pick(split: str, sel: str) -> list[dict]:
    cs = L.clues(split)
    if sel == "all":
        return cs
    if sel.startswith("n"):
        return cs[: int(sel[1:])]
    if sel in ("miss2", "hit2"):
        out = []
        for c in cs:
            rs = [L.cached(L.base_body(c["text"], ["en"] + OTHERS, subjects={"enable": False})), L.cached(L.base_body(c["text"], ["zh", "id"]))]
            missed = None in rs or all(L.rank_of(c, r["data"].get("results") or []) is None for r in rs)
            if missed == (sel == "miss2"):
                out.append(c)
        return out if sel == "miss2" else out[::7]
    if sel == "miss":
        out = []
        for c in cs:
            rs = [L.cached(L.base_body(c["text"], lang)) for lang in (None, ["zh", "id"])]
            if None in rs or all(L.rank_of(c, r["data"].get("results") or []) is None for r in rs):
                out.append(c)
        return out
    ids = set(sel.split(","))
    return [c for c in cs if c["id"] in ids]


def main() -> None:
    variants, split, sel = sys.argv[1].split(","), sys.argv[2], sys.argv[3]
    cs = pick(split, sel)
    live = 0
    for v in variants:
        ranks, secs, lats, n = [], [], [], 0
        for c in cs:
            body = VARIANTS[v](c)
            raw = L.octen_cached.search(body)
            if raw.get("code", 0) != 0:
                print("  ERROR", v, c["id"], raw.get("code"), raw.get("msg"))
                continue
            live += not raw["_cached"]
            res = raw["data"].get("results") or []
            ranks.append(L.rank_of(c, res))
            secs.append(raw["_secs"])
            lats.append((raw.get("meta") or {}).get("latency") or 0)
            n += 1
        found = [r for r in ranks if r is not None]
        ss, ll = sorted(secs), sorted(lats)
        print(f"{v:<14} n={n} story-url in results {len(found)}/{n}  at1 {sum(r == 1 for r in found)}  <=10 {sum(r <= 10 for r in found)}  <=20 {sum(r <= 20 for r in found)}  "
              f"secs median {ss[len(ss) // 2]:.3f} p90 {ss[int(len(ss) * 0.9)]:.3f}  server-ms median {ll[len(ll) // 2]:.0f} p90 {ll[int(len(ll) * 0.9)]:.0f}")
    print("live calls made:", live, " spend:", L.octen_cached.spend().get("tags", {}).get("params"))


if __name__ == "__main__":
    main()
