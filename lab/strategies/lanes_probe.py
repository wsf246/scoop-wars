"""Probe for the `lanes` study: run single lanes over clues (cached, counted against LAB_TAG) and tabulate
where the key article shows up, per lane and per pair of lanes. Not a strategy.

    LAB_TAG=lanes LAB_TAG_MAX=400 LAB_QPS=2 uv run python lab/strategies/lanes_probe.py --split mini --lanes any,zh+id,en
        [--only-language zh,id] [--only-groups plain,minimal] [--cached-only] [--pairs any|zh+id,en|zh+id]
"""

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(LAB_DIR), str(LAB_DIR.parent)]

import octen_cached  # noqa: E402
import play  # noqa: E402
from strategies import lanes_common as lc  # noqa: E402


def cached_path(b: dict) -> Path:
    return octen_cached.CACHE_DIR / (hashlib.sha1(json.dumps(["news-search", b], sort_keys=True, ensure_ascii=False).encode()).hexdigest() + ".json")


def fetch(clue: dict, lane: str, cached_only: bool) -> dict | None:
    b = lc.body(clue["text"], lane)
    if cached_only and not cached_path(b).exists():
        return None
    raw = octen_cached.search(b)
    if raw.get("code", 0) != 0:
        print("search failed", clue["id"], lane, raw.get("code"), raw.get("msg"), file=sys.stderr)
        return None
    return raw


def lane_row(clue: dict, raw: dict) -> dict:
    """What one lane returned for one clue: rank of the first key article, rank of the source url, languages."""
    results = (raw.get("data") or {}).get("results") or []
    urls = [play.normalize_url(a.get("url") or "") for a in results]
    key = {play.normalize_url(a["url"]) for a in clue["key"]["articles"]}
    src = play.normalize_url(clue["source"]["url"])
    need = clue.get("source_language")
    langs = [lc.language_of(f"{a.get('title') or ''} {(a.get('highlight') or '')[:500]}") for a in results]
    key_ranks = [i + 1 for i, u in enumerate(urls) if u in key and (not need or langs[i] in need)]
    return {
        "n": len(results), "key_rank": key_ranks[0] if key_ranks else None, "key_urls": {u for u in urls if u in key},
        "src_rank": urls.index(src) + 1 if src in urls else None, "langs": langs, "secs": raw.get("_secs", 0), "urls": urls,
    }


def load(split: str) -> list[dict]:
    clues = json.loads((LAB_DIR / "clues.json").read_text(encoding="utf-8"))
    if split == "mini":
        return [c for c in clues if c.get("mini")]
    assert split == "dev", "mini and dev only"
    return [c for c in clues if c["split"] == "dev"]


def collect(clues: list[dict], lanes: list[str], cached_only: bool = False, parallel: int = 4) -> dict:
    """{clue id: {lane: lane_row or None}}"""
    jobs = [(c, lane) for c in clues for lane in lanes]
    with ThreadPoolExecutor(parallel) as ex:
        raws = list(ex.map(lambda j: fetch(j[0], j[1], cached_only), jobs))
    out: dict = {}
    for (c, lane), raw in zip(jobs, raws):
        out.setdefault(c["id"], {})[lane] = lane_row(c, raw) if raw else None
    return out


def table(clues: list[dict], data: dict, columns: list[str]) -> None:
    """columns: lane names or pairs "a|b" (key in either). Real clues only; fakes use the source article."""
    groups = sorted({c["group"] for c in clues})
    print(f"{'group':<16}{'n':>4}  " + "  ".join(f"{col:>12}" for col in columns))
    for g in groups + ["ALL REAL", "ALL"]:
        rows = [c for c in clues if (c["group"] == g or (g == "ALL REAL" and not c["fake"]) or g == "ALL")]
        cells = []
        for col in columns:
            lanes = col.split("|")
            have = [c for c in rows if all(data[c["id"]].get(l) is not None for l in lanes)]
            field = lambda c: "src_rank" if c["fake"] else "key_rank"  # noqa: E731
            hit = sum(any(data[c["id"]][l][field(c)] for l in lanes) for c in have)
            cells.append(f"{hit:>3}/{len(have):<3}{(100 * hit / len(have) if have else 0):>4.0f}%")
        print(f"{g:<16}{len(rows):>4}  " + "  ".join(f"{cell:>12}" for cell in cells))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="mini")
    p.add_argument("--lanes", default="any,zh+id")
    p.add_argument("--pairs", default="")
    p.add_argument("--only-language", default="")
    p.add_argument("--only-groups", default="")
    p.add_argument("--ids", default="")
    p.add_argument("--cached-only", action="store_true")
    p.add_argument("--detail", action="store_true")
    args = p.parse_args()
    clues = load(args.split)
    if args.only_language:
        clues = [c for c in clues if c["language"] in args.only_language.split(",")]
    if args.only_groups:
        clues = [c for c in clues if c["group"] in args.only_groups.split(",")]
    if args.ids:
        clues = [c for c in clues if c["id"] in args.ids.split(",")]
    lanes = args.lanes.split(",")
    data = collect(clues, lanes, args.cached_only)
    table(clues, data, lanes + [x for x in args.pairs.split(",") if x])
    if args.detail:
        for c in clues:
            d = data[c["id"]]
            cells = "  ".join(f"{l}: key@{d[l]['key_rank'] if d[l] else '-'} src@{d[l]['src_rank'] if d[l] else '-'} [{','.join(d[l]['langs']) if d[l] else ''}]" for l in lanes)
            print(f"{c['id']} {c['group']:<14} {c['language']} route={lc.route(c['text'])} | {cells}\n      {c['text'][:150]}")
    print("spend:", octen_cached.spend().get("tags", {}).get(octen_cached.TAG), "live calls for tag", octen_cached.TAG)


if __name__ == "__main__":
    main()
