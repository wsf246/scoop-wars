"""Bench: score a search strategy on the lab's clues, the way the game scores.

    uv run python lab/bench.py run baseline --split mini          # 60 clues, quick
    uv run python lab/bench.py run baseline --split dev           # 150 clues
    uv run python lab/bench.py run mine --split dev --no-llm      # retrieval only: no Claude calls
    uv run python lab/bench.py run mine --split dev --types fake,twin --limit 40
    uv run python lab/bench.py compare <run file> <run file>
    uv run python lab/bench.py show <run file> --verdict wrong    # the failures, with what was picked

A strategy is lab/strategies/<name>.py with one function:

    def answer(clue: str, tools: Tools) -> dict     # {"url": "...", "summary": "..."}; url "" = blank

It may use only `tools`: tools.search / tools.search_many (2 search calls per clue, as in the game),
tools.ask (one Claude call), tools.octen (Extract or Embedding: allowed by the rules, outside the search budget).
Searches and Claude calls are cached on disk, so a re-run of an unchanged step is free and instant; the time
reported per clue is rebuilt from the latency recorded when each call was live.

Scoring: a fake clue is right when the answer is blank (+2) and wrong when it links anything (-2). A real clue
is right (+2) when the link is in the answer key, or reports the same event according to a Claude judge, and is
in the required language when the clue has one; wrong (-2) otherwise; blank scores 0.
"""

import argparse
import fcntl
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
ROOT = LAB_DIR.parent
sys.path[:0] = [str(LAB_DIR), str(ROOT)]

import octen_cached  # noqa: E402
import play  # noqa: E402

RUNS_DIR = LAB_DIR / "runs"
LLM_CACHE = LAB_DIR / "cache_llm"
CARD_SCHEMA = json.loads((ROOT / "card.schema.json").read_text(encoding="utf-8"))
TAG = os.environ.get("LAB_TAG", "main")
LLM_TAG_MAX = int(os.environ.get("LAB_LLM_MAX", "100000"))  # live Claude calls this tag may make
_llm_slots = threading.Semaphore(int(os.environ.get("LAB_LLM_PARALLEL", "4")))  # the live game shares this Claude account


# --- Claude, cached -----------------------------------------------------------------------
def _count_llm_call(tag: str) -> None:
    LLM_CACHE.mkdir(exist_ok=True)
    with open(LLM_CACHE / "_spend.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = LLM_CACHE / "_spend.json"
        try:
            spent = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            spent = {}
        if tag != "judge" and spent.get(tag, 0) >= LLM_TAG_MAX:
            raise RuntimeError(f"Claude allowance used for {tag}: {LLM_TAG_MAX} live calls. Use a smaller --split or --limit, or re-run unchanged steps (cached).")
        spent[tag] = spent.get(tag, 0) + 1
        path.write_text(json.dumps(spent), encoding="utf-8")


def llm(system: str, prompt: str, schema: dict, model: str = "sonnet", effort: str = "low", timeout: float = 60, tag: str = TAG) -> tuple[dict | None, float, str]:
    """One `claude -p` call with no tools, answering in `schema`. Returns (answer, seconds, error). Cached."""
    LLM_CACHE.mkdir(exist_ok=True)
    digest = hashlib.sha1(json.dumps([system, prompt, schema, model, effort], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = LLM_CACHE / f"{digest}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        return saved["answer"], saved["secs"], ""
    _count_llm_call(tag)
    cmd = [
        play.CLAUDE, "-p", "--model", model, "--system-prompt", system, "--output-format", "json", "--json-schema", json.dumps(schema),
        "--mcp-config", '{"mcpServers": {}}', "--strict-mcp-config", "--tools", "", "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence",
    ]
    if effort:
        cmd += ["--effort", effort]
    workdir = tempfile.mkdtemp(prefix="scoop-lab-")
    with _llm_slots:
        started = time.time()
        answer, error = play.run_claude(cmd, prompt, timeout, workdir)
        secs = round(time.time() - started, 2)
    shutil.rmtree(workdir, ignore_errors=True)
    if answer is not None:
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"answer": answer, "secs": secs}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return answer, secs, error


# --- what a strategy may use ----------------------------------------------------------------
def _article(a: dict) -> dict:
    return {"title": a.get("title"), "url": a.get("url"), "published": a.get("time_published"), "text": (a.get("highlight") or a.get("full_content") or "")}


class Tools:
    """One clue's tools. `clock` is the seconds the clue has taken so far (searches run together count once)."""

    def __init__(self) -> None:
        self.clock = 0.0
        self.searches: list[dict] = []  # every search made: {"name", "body", "api", "articles", "secs", "data"}
        self.llm_calls = 0
        self.llm_error = ""

    def search_many(self, requests: list[dict]) -> list[dict]:
        """Run searches at once. Each request: {"name": str, "body": News Search body, "api": "news-search"}.

        Returns the same list with "articles" ([{title, url, published, text}]), "secs" and "data" (Octen's raw
        "data": results, subjects) added. The game allows 2 search calls per clue: a third raises.
        """
        if len(self.searches) + len(requests) > 2:
            raise RuntimeError("Search budget: 2 search calls per clue, all search APIs combined.")

        def one(r: dict) -> dict:
            raw = octen_cached.search(r["body"], r.get("api", "news-search"))
            data = raw.get("data") or {}
            return {**r, "articles": [_article(a) for a in data.get("results") or []], "secs": raw.get("_secs", 0.6), "data": data, "code": raw.get("code", 0)}

        with ThreadPoolExecutor(len(requests)) as ex:
            done = list(ex.map(one, requests))
        self.clock += max(d["secs"] for d in done)
        self.searches += done
        return done

    def search(self, body: dict, name: str = "search", api: str = "news-search") -> list[dict]:
        """One search; returns its articles."""
        return self.search_many([{"name": name, "body": body, "api": api}])[0]["articles"]

    def ask(self, system: str, prompt: str, schema: dict = CARD_SCHEMA, model: str = "sonnet", effort: str = "low", timeout: float = 40) -> dict | None:
        """One Claude call (no tools) answering in `schema`. None when the call failed."""
        answer, secs, error = llm(system, prompt, schema, model, effort, timeout)
        self.clock += secs
        self.llm_calls += 1
        self.llm_error = error or self.llm_error
        return answer

    def octen(self, api: str, body: dict) -> dict:
        """Octen Extract ("extract") or Embedding ("embedding"): allowed in the game, outside the search budget."""
        if api not in ("extract", "embedding"):
            raise RuntimeError("Only extract and embedding are allowed outside the search budget.")
        raw = octen_cached.search(body, api)
        self.clock += raw.get("_secs", 0.5)
        return raw


# --- clues and scoring ---------------------------------------------------------------------------
def load_clues(split: str = "dev", types: str = "", limit: int = 0) -> list[dict]:
    clues = json.loads((LAB_DIR / "clues.json").read_text(encoding="utf-8"))
    if split == "mini":
        clues = [c for c in clues if c.get("mini")]
    elif split in ("dev", "test"):
        clues = [c for c in clues if c["split"] == split]
    if types:
        clues = [c for c in clues if c["group"] in types.split(",")]
    return clues[:limit] if limit else clues


_ID_WORDS = {"yang", "dan", "di", "ke", "dari", "untuk", "dengan", "pada", "ini", "itu", "akan", "tidak", "dalam", "oleh", "juga", "sebagai", "telah", "karena"}


def language_of(text: str, lane_languages: set | None = None) -> str:
    """A rough guess of an article's language: zh, id, or other.

    From its title and opening, helped by the language filters of the searches that returned it: an article
    with no Chinese characters that came back from a Chinese-or-Indonesian search is Indonesian.
    """
    text = text or ""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cjk >= 4 and not re.search(r"[\u3040-\u30ff\uac00-\ud7af]", text):
        return "zh"
    if lane_languages and "id" in lane_languages and lane_languages <= {"zh", "id"}:
        return "id"
    words = re.findall(r"[a-z]+", text.lower())
    if sum(w in _ID_WORDS for w in words) >= 2:
        return "id"
    return "other"


JUDGE_SCHEMA = {"type": "object", "properties": {"same_event": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["same_event", "reason"], "additionalProperties": False}
JUDGE_SYSTEM = (
    "You compare two news articles. Decide whether article B reports the same specific event as article A: the same incident, "
    "announcement, match or decision, not merely the same topic, an earlier stage, or a similar event elsewhere. "
    "If B's text is too thin to tell, answer false."
)


def same_event(clue: dict, picked: dict) -> tuple[bool, str]:
    src = clue["source"]
    prompt = f"Article A\ntitle: {src['title']}\npublished: {src['time_published']}\ntext: {src['text'][:900]}\n\nArticle B\ntitle: {picked['title']}\npublished: {picked['published']}\ntext: {(picked['text'] or '')[:900]}\n"
    answer, _, error = llm(JUDGE_SYSTEM, prompt, JUDGE_SCHEMA, "sonnet", "low", 60, tag="judge")
    if answer is None:
        return False, f"judge failed: {error}"
    return bool(answer.get("same_event")), str(answer.get("reason") or "")[:200]


def key_urls(clue: dict) -> set[str]:
    return {play.normalize_url(a["url"]) for a in clue["key"]["articles"]}


def score(clue: dict, answer: dict | None, tools: Tools, error: str = "") -> dict:
    url = str((answer or {}).get("url") or "").strip()
    seen = {}
    for s in tools.searches:
        for rank, a in enumerate(s["articles"], start=1):
            if a.get("url"):
                entry = seen.setdefault(play.normalize_url(a["url"]), {**a, "lane": s["name"], "rank": rank, "langs": None})
                filt = set(s["body"].get("language") or [])
                if filt and (entry["langs"] is None or len(filt) < len(entry["langs"])):
                    entry["langs"] = filt  # the narrowest language filter that returned this article
    key = key_urls(clue)
    ranks = [seen[u]["rank"] for u in key if u in seen]
    row = {
        "id": clue["id"], "group": clue["group"], "fake": clue["fake"], "clue": clue["text"], "url": url, "summary": str((answer or {}).get("summary") or "")[:300],
        "key_rank": min(ranks) if ranks else None, "searches": len(tools.searches), "llm_calls": tools.llm_calls, "secs": round(tools.clock, 2),
        "error": error or tools.llm_error, "reason": "",
    }
    if clue["fake"]:
        row["verdict"] = "wrong" if url else "right"
        row["reason"] = f"fake clue ({clue.get('changed')})" if url else ""
    elif not url:
        row["verdict"] = "blank"
    else:
        picked = seen.get(play.normalize_url(url))
        need = clue.get("source_language")
        if picked is None:
            row["verdict"], row["reason"] = "wrong", "the link is not one of the search results"
        elif need and language_of(f"{picked['title']} {picked['text'][:600]}", picked["langs"]) not in need:
            row["verdict"], row["reason"] = "wrong", f"the clue needs an article in {need}"
        elif play.normalize_url(url) in key:
            row["verdict"] = "right"
        else:
            ok, why = same_event(clue, picked)
            row["verdict"], row["reason"] = ("right" if ok else "wrong"), f"judge: {why}"
        row["picked_title"] = (picked or {}).get("title")
    row["points"] = {"right": 2, "wrong": -2, "blank": 0}[row["verdict"]]
    return row


def summarize(rows: list[dict]) -> dict:
    def part(rs: list[dict]) -> dict:
        n = len(rs) or 1
        return {"n": len(rs), "right": sum(r["verdict"] == "right" for r in rs), "wrong": sum(r["verdict"] == "wrong" for r in rs),
                "blank": sum(r["verdict"] == "blank" for r in rs), "points": sum(r["points"] for r in rs), "right_pct": round(100 * sum(r["verdict"] == "right" for r in rs) / n, 1)}

    real = [r for r in rows if not r["fake"]]
    secs = sorted(r["secs"] for r in rows) or [0]
    out = part(rows)
    out.update({
        "median_secs": secs[len(secs) // 2], "p90_secs": secs[int(len(secs) * 0.9)] if len(secs) > 1 else secs[0], "max_secs": secs[-1],
        "key_in_results_pct": round(100 * sum(r["key_rank"] is not None for r in real) / (len(real) or 1), 1),
        "key_at_1_pct": round(100 * sum(r["key_rank"] == 1 for r in real) / (len(real) or 1), 1),
        "errors": sum(bool(r["error"]) for r in rows),
        "groups": {g: part([r for r in rows if r["group"] == g]) for g in sorted({r["group"] for r in rows})},
    })
    return out


def print_summary(name: str, s: dict) -> None:
    print(f"\n{name}: right {s['right']}/{s['n']} ({s['right_pct']}%)  wrong {s['wrong']}  blank {s['blank']}  points {s['points']:+d}")
    print(f"  time: median {s['median_secs']:.1f}s  p90 {s['p90_secs']:.1f}s  max {s['max_secs']:.1f}s   key article in results {s['key_in_results_pct']}% (rank 1: {s['key_at_1_pct']}%)   errors {s['errors']}")
    for g, p in s["groups"].items():
        print(f"  {g:<15} right {p['right']:>3}/{p['n']:<3} ({p['right_pct']:>5}%)  wrong {p['wrong']:>3}  blank {p['blank']:>3}  points {p['points']:+d}")


# --- commands ----------------------------------------------------------------------------------------
def cmd_run(args) -> None:
    strategy = importlib.import_module(f"strategies.{args.strategy}")
    clues = load_clues(args.split, args.types, args.limit)

    def one(clue: dict) -> dict:
        tools = Tools()
        answer, error = None, ""
        try:
            if args.no_llm:
                strategy.retrieve(clue["text"], tools)  # searches only
            else:
                answer = strategy.answer(clue["text"], tools)
        except Exception as e:  # noqa: BLE001 - a strategy that crashes scores a blank, as in the game
            error = f"{type(e).__name__}: {e}"[:300]
        return score(clue, answer, tools, error)

    started = time.time()
    with ThreadPoolExecutor(args.parallel) as ex:
        rows = list(ex.map(one, clues))
    summary = summarize(rows)
    RUNS_DIR.mkdir(exist_ok=True)
    out = RUNS_DIR / f"{args.strategy}-{args.split}{'-noLLM' if args.no_llm else ''}-{datetime.now():%H%M%S}.json"
    out.write_text(json.dumps({"strategy": args.strategy, "split": args.split, "no_llm": args.no_llm, "summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print_summary(f"{args.strategy} on {args.split} ({len(clues)} clues, {time.time() - started:.0f}s wall)", summary)
    if args.no_llm:
        print("  (retrieval only: right/wrong/points are not meaningful, read the 'key article in results' line)")
    print(f"  saved {out.relative_to(ROOT)}   octen live calls so far: {octen_cached.spend()}")


def cmd_compare(args) -> None:
    a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.before, args.after))
    print_summary(f"BEFORE {a['strategy']}", a["summary"])
    print_summary(f"AFTER  {b['strategy']}", b["summary"])
    old = {r["id"]: r for r in a["rows"]}
    changed = [(old[r["id"]], r) for r in b["rows"] if r["id"] in old and old[r["id"]]["verdict"] != r["verdict"]]
    print(f"\n{len(changed)} clues changed verdict:")
    for o, r in changed[: args.max]:
        print(f"  {r['id']} [{r['group']}] {o['verdict']} -> {r['verdict']}: {r['clue'][:90]}")


def cmd_show(args) -> None:
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    rows = [r for r in run["rows"] if (not args.verdict or r["verdict"] == args.verdict) and (not args.group or r["group"] == args.group)]
    for r in rows[: args.max]:
        print(f"{r['id']} [{r['group']}] {r['verdict']} key_rank={r['key_rank']} {r['secs']}s\n   clue: {r['clue']}\n   picked: {r.get('picked_title') or r['url'] or '(blank)'}\n   summary: {r['summary'][:160]}\n   why: {r['reason'] or r['error']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("strategy")
    r.add_argument("--split", default="mini", choices=["mini", "dev", "test", "all"])
    r.add_argument("--types", default="", help="comma-separated groups: plain,local_zh,local_id,fake,twin,minimal,other_language")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--parallel", type=int, default=6)
    r.add_argument("--no-llm", action="store_true", help="retrieval only: call the strategy's retrieve(clue, tools)")
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--max", type=int, default=40)
    c.set_defaults(fn=cmd_compare)
    s = sub.add_parser("show")
    s.add_argument("run")
    s.add_argument("--verdict", default="")
    s.add_argument("--group", default="")
    s.add_argument("--max", type=int, default=30)
    s.set_defaults(fn=cmd_show)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
