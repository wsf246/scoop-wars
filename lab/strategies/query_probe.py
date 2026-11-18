"""Probe for the query study: run query variants over clues (cached, counted against LAB_TAG) and tabulate at
which rank the key article shows up, per variant and per group. Not a strategy.

    LAB_TAG=query LAB_TAG_MAX=350 LAB_LLM_MAX=250 LAB_QPS=2 LAB_LLM_PARALLEL=2 uv run python lab/strategies/query_probe.py \
        --split mini --variants raw_any,raw_zhid,en_any [--groups plain,minimal] [--ids c001,c002] [--cached-only] [--no-rewrite] [--show]

A variant is "<query>_<lane>": query in raw (the clue), kw (keywords), en (haiku q_en), loc (haiku q_local),
enkw (keywords of q_en), title (the source article's title: an index check); lane in any (no filter),
zhid ([zh, id]), lang ([the language haiku guessed]), src ([the source article's language]: an oracle).
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(LAB_DIR), str(LAB_DIR.parent)]

import bench  # noqa: E402
import octen_cached  # noqa: E402
import play  # noqa: E402
from strategies import query_lib as ql  # noqa: E402


class _Ask:
    """tools.ask without a clock: the rewrite goes through bench.llm (cached, counted)."""

    def __init__(self, cached_only: bool) -> None:
        self.cached_only = cached_only
        self.secs = None

    def ask(self, system, prompt, schema, model="sonnet", effort="low", timeout=40):
        import hashlib

        digest = hashlib.sha1(json.dumps([system, prompt, schema, model, effort], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if self.cached_only and not (bench.LLM_CACHE / f"{digest}.json").exists():
            return None
        answer, secs, _ = bench.llm(system, prompt, schema, model, effort, timeout)
        self.secs = secs
        return answer


def targets(clue: dict) -> tuple[set, set]:
    arts = clue["key"]["articles"] + [clue["source"]]
    return {play.normalize_url(a["url"]) for a in arts}, {(a.get("title") or "").strip() for a in arts if len((a.get("title") or "").strip()) > 25}


def hit_rank(clue: dict, results: list[dict]) -> int | None:
    urls, titles = targets(clue)
    for i, a in enumerate(results, start=1):
        if play.normalize_url(a.get("url") or "") in urls or (a.get("title") or "").strip() in titles:
            return i
    return None


def variant_body(variant: str, clue: dict, rw: dict | None) -> dict | None:
    """The search body for this variant, or None when it does not apply to this clue."""
    qname, lane = variant.rsplit("_", 1)
    count = 10
    if lane.endswith("50"):
        lane, count = lane[:-2], 50
    if qname == "raw":
        q = clue["text"]
    elif qname == "kw":
        q = ql.keywords(clue["text"])
    elif qname == "title":
        q = clue["source"]["title"]
    else:
        if not rw:
            return None
        q = {"en": rw["q_en"], "loc": rw["q_local"] or "", "enkw": ql.keywords(rw["q_en"])}[qname]
    if not q.strip():
        return None
    if lane == "any":
        language = None
    elif lane == "zhid":
        language = ["zh", "id"]
    elif lane == "lang":
        if not rw or rw["lang"] == "en":
            return None
        language = [rw["lang"]]
    elif lane == "src":
        if clue.get("language") not in ql.API_LANGS:
            return None
        language = [clue["language"]]
    else:
        language = lane.split("+")
    return ql.body(q, language, count)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="mini")
    p.add_argument("--variants", required=True)
    p.add_argument("--groups", default="")
    p.add_argument("--ids", default="")
    p.add_argument("--langs", default="", help="only clues whose haiku-guessed lang is one of these (prefix ! to exclude)")
    p.add_argument("--cached-only", action="store_true")
    p.add_argument("--no-rewrite", action="store_true")
    p.add_argument("--show", action="store_true")
    p.add_argument("--save", default="")
    args = p.parse_args()
    assert args.split in ("mini", "dev"), "mini and dev only"
    clues = bench.load_clues(args.split, args.groups)
    if args.ids:
        clues = [c for c in clues if c["id"] in args.ids.split(",")]
    variants = args.variants.split(",")

    def one(clue: dict) -> dict:
        rw, rw_secs = None, None
        if not args.no_rewrite:
            asker = _Ask(args.cached_only)
            rw = ql.rewrite(clue["text"], asker)
            rw_secs = asker.secs
        row = {"id": clue["id"], "group": clue["group"], "fake": clue["fake"], "rw": rw, "rw_secs": rw_secs, "v": {}}
        if args.langs and rw:
            want = args.langs.lstrip("!").split(",")
            if (rw["lang"] in want) == args.langs.startswith("!"):
                return row
        for v in variants:
            b = variant_body(v, clue, rw)
            if b is None:
                continue
            if args.cached_only and not octen_cached._key("news-search", b).exists():
                continue
            raw = octen_cached.search(b)
            if raw.get("code", 0) != 0:
                print("search failed", clue["id"], v, raw.get("code"), raw.get("msg"), file=sys.stderr)
                continue
            results = (raw.get("data") or {}).get("results") or []
            row["v"][v] = {"rank": hit_rank(clue, results), "n": len(results), "secs": raw.get("_secs"), "query": b["query"], "top": [(a.get("title") or "")[:70] for a in results[:3]]}
        return row

    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(one, clues))

    real = [r for r in rows if not r["fake"]]
    print(f"\n{args.split}: {len(rows)} clues ({len(real)} real). hit = a key or source article (url or exact title) in the 10 results")
    groups = sorted({r["group"] for r in real})
    print(f"{'variant':<12} {'n':>4} {'hit':>4} {'hit%':>6} {'@1':>4} {'@1%':>6} {'med s':>6}   " + "  ".join(f"{g}" for g in groups))
    for v in variants:
        rs = [r for r in real if v in r["v"]]
        if not rs:
            print(f"{v:<12} (no clue)")
            continue
        hit = sum(r["v"][v]["rank"] is not None for r in rs)
        top = sum(r["v"][v]["rank"] == 1 for r in rs)
        per = defaultdict(list)
        for r in rs:
            per[r["group"]].append(r["v"][v]["rank"])
        cells = "  ".join(f"{g}:{sum(x is not None for x in per[g])}/{len(per[g])}(@1 {sum(x == 1 for x in per[g])})" for g in groups if per[g])
        secs = statistics.median(r["v"][v]["secs"] or 0 for r in rs)
        print(f"{v:<12} {len(rs):>4} {hit:>4} {100 * hit / len(rs):>5.1f}% {top:>4} {100 * top / len(rs):>5.1f}% {secs:>6.2f}   {cells}")
    rws = [r["rw_secs"] for r in rows if r["rw_secs"]]
    if rws:
        rws.sort()
        print(f"rewrite call (haiku): n {len(rws)}  median {statistics.median(rws):.2f}s  p90 {rws[int(len(rws) * 0.9)]:.2f}s  max {rws[-1]:.2f}s")
    if args.show:
        for r in rows:
            cells = "  ".join(f"{v}={r['v'][v]['rank']}" for v in variants if v in r["v"])
            rw = r["rw"] or {}
            print(f"{r['id']} [{r['group']}] {rw.get('country')}/{rw.get('lang')}  {cells}\n     en: {rw.get('q_en')}\n     loc: {rw.get('q_local')}")
    if args.save:
        Path(args.save).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("octen spend:", octen_cached.spend().get("tags", {}).get(octen_cached.TAG), " llm spend:", json.loads((bench.LLM_CACHE / "_spend.json").read_text()).get(bench.TAG))


if __name__ == "__main__":
    main()
