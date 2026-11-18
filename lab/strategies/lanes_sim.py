"""Offline simulation for the `lanes` study: given cached lane results, how many key articles does each way
of choosing the two calls reach? Not a strategy. Reads the cache only (no live calls).

    uv run python lab/strategies/lanes_sim.py --split dev
"""

import argparse
import collections
import re
import sys
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(LAB_DIR), str(LAB_DIR.parent)]

import octen_cached  # noqa: E402
from strategies import lanes_common as lc  # noqa: E402
from strategies import lanes_probe as lp  # noqa: E402


def any_text(clue: dict, k: int = 5) -> str:
    raw = octen_cached.search(lc.body(clue["text"], "any"))
    return " ".join(f"{a.get('title') or ''} {(a.get('highlight') or '')[:400]}" for a in (raw.get("data") or {}).get("results", [])[:k])


def policies(clue: dict, d: dict) -> dict:
    """{policy name: (lane, lane)}"""
    flags = lc.route(clue["text"])
    local = [f for f in flags if f in ("zh", "id")]
    other = [f for f in flags if f not in ("zh", "id")]
    out = {
        "baseline any+zh+id": ("any", "zh+id"),
        "any+wide": ("any", "wide"),
        "zh+id+wide (no any)": ("zh+id", "wide"),
        "rule: other place -> wide, else zh+id": ("any", "wide" if other and not local else "zh+id"),
        "rule: zh/id place -> zh+id, else wide": ("any", "zh+id" if local else "wide"),
    }
    # sequential, wide first: the languages of its top 10 decide the second call
    wl = collections.Counter(d["wide"]["langs"]) if d.get("wide") else collections.Counter()
    top, n = (wl.most_common(1) or [("", 0)])[0]
    zi = wl["zh"] + wl["id"]
    out["seq wide-first: zh/id>=6 -> zh+id else any"] = ("wide", "zh+id" if zi >= 6 else "any")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="dev")
    args = p.parse_args()
    clues = lp.load(args.split)
    data = lp.collect(clues, ["any", "zh+id", "wide"], cached_only=True)
    clues = [c for c in clues if all(data[c["id"]].get(l) for l in ("any", "zh+id", "wide"))]
    groups = sorted({c["group"] for c in clues})
    names = list(policies(clues[0], data[clues[0]["id"]]).keys()) + ["oracle second lane"]
    counts = {n: collections.Counter() for n in names}
    for c in clues:
        d = data[c["id"]]
        f = "src_rank" if c["fake"] else "key_rank"
        for n, lanes in policies(c, d).items():
            counts[n][c["group"]] += any(d[l][f] for l in lanes)
        counts["oracle second lane"][c["group"]] += any(d[l][f] for l in ("any", "zh+id", "wide"))
    size = collections.Counter(c["group"] for c in clues)
    real = [g for g in groups if g != "fake"]
    print(f"{args.split}: {len(clues)} clues, {sum(size[g] for g in real)} real. Key article reached (fake: the real source article reached)")
    print(f"{'policy':<48}" + "".join(f"{g[:9] + ' ' + str(size[g]):>13}" for g in groups) + f"{'REAL':>10}")
    for n in names:
        tot = sum(counts[n][g] for g in real)
        print(f"{n:<48}" + "".join(f"{counts[n][g]:>13}" for g in groups) + f"{tot:>6} {100 * tot / sum(size[g] for g in real):.1f}%")


if __name__ == "__main__":
    main()
