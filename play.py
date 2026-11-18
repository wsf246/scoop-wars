"""Scoop Wars starter kit: the house agent.

  uv run play.py doctor              check Codex, your keys and the game server
  uv run play.py join "Team Name"    register a team (only if the help desk asks; teams come from the event page)
  uv run play.py ask "some clue"     run the agent on one clue
  uv run play.py eval                score the agent on every practice clue, the way the game does
  uv run play.py eval --compare      ...and show what changed since your last eval
  uv run play.py ui                  the same eval as a live dashboard in your browser
  uv run play.py play                join the Scoop clock; then hands off the keyboard

The agent is Codex (`codex exec`) with one tool, Octen News Search (news_search_mcp.py),
and the instructions in prompt.md. Change any of it, or replace run_agent() with an
agent of your own: every search must still go through octen_client (news_search(),
or search() for the other Octen search APIs; 2 calls per clue, all APIs combined).
"""

import argparse
import contextvars
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import octen_client

KIT_DIR = Path(__file__).resolve().parent
TEAM_FILE = KIT_DIR / ".scoop.json"
PROMPT_FILE = KIT_DIR / "prompt.md"
SCHEMA_FILE = KIT_DIR / "card.schema.json"
MCP_FILE = KIT_DIR / "news_search_mcp.py"
RUNS_DIR = KIT_DIR / "runs"
GAME_TIMEOUT = 41  # the game gives 45s; play.py stops the agent with a few seconds to submit


# --- config ----------------------------------------------------------------------
def load_env() -> None:
    path = KIT_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
# Windows consoles default to cp1252: never crash printing a Chinese or Indonesian query or headline
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")
CODEX = os.environ.get("CODEX_BIN") or shutil.which("codex") or "codex"
CODEX_MODEL = os.environ.get("CODEX_MODEL", "")  # empty = Codex default
CODEX_REASONING = os.environ.get("CODEX_REASONING", "low")  # low | medium | high (check what your model supports)
AGENT = os.environ.get("SCOOP_AGENT", "claude").lower()  # claude | codex: which CLI answers the clues and judges eval
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
CLAUDE_MODEL = os.environ.get("SCOOP_CLAUDE_MODEL", "sonnet")  # an alias (haiku, sonnet, opus) or a full model name
CLAUDE_EFFORT = os.environ.get("SCOOP_CLAUDE_EFFORT", "low")  # low | medium | high; empty = the model's default


def team() -> dict:
    """Your team's token: SCOOP_TOKEN in .env (from the event page), or .scoop.json from `play.py join`."""
    if os.environ.get("SCOOP_TOKEN"):
        return {"token": os.environ["SCOOP_TOKEN"].strip()}
    return json.loads(TEAM_FILE.read_text(encoding="utf-8")) if TEAM_FILE.exists() else {}


def team_name() -> str:
    t = team()
    if t.get("name"):
        return t["name"]
    try:
        return api("GET", "/api/team", timeout=5).get("name") or "your team"
    except ServerError:
        return "your team"


def server_url() -> str:
    url = os.environ.get("SCOOP_SERVER") or team().get("server") or ""
    return url.rstrip("/")


# --- game server client ------------------------------------------------------------
class ServerError(Exception):
    pass


def api(method: str, path: str, body: dict | None = None, timeout: float = 10, admin: bool = False) -> dict | list:
    base = server_url()
    if not base:
        raise ServerError("SCOOP_SERVER is not set. Put the URL from the big screen in .env")
    headers = {"content-type": "application/json"}
    if team().get("token"):
        headers["x-scoop-token"] = team()["token"]
    if admin and os.environ.get("SCOOP_ADMIN_TOKEN"):
        headers["x-admin-token"] = os.environ["SCOOP_ADMIN_TOKEN"]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except Exception:
            msg = e.reason
        raise ServerError(f"{e.code}: {msg}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ServerError(f"can't reach {base}: {e}") from None


# --- the agent ----------------------------------------------------------------------
@dataclass
class Card:
    url: str = ""
    summary: str = ""
    seconds: float = 0
    searches: list[str] = field(default_factory=list)  # successful search calls (octen_client.label)
    refused: int = 0  # calls refused by the search budget
    search_times: list[dict] = field(default_factory=list)  # [{"t": secs since start, "ok": bool}]
    error: str = ""


def codex_command(workdir: str, out_file: str, run_log: str) -> list[str]:
    toml = json.dumps  # JSON strings are valid TOML basic strings
    cmd = [
        CODEX, "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",  # your personal Codex config can't add other tools
        "--sandbox", "read-only",
        "--color", "never",
        "--cd", workdir,
        "--output-schema", str(SCHEMA_FILE),
        "--output-last-message", out_file,
        "-c", f"mcp_servers.octen.command={toml(sys.executable)}",
        "-c", f"mcp_servers.octen.args=[{toml(str(MCP_FILE))}]",
        "-c", f"mcp_servers.octen.env={{SCOOP_RUN_LOG={toml(run_log)}}}",
        "-c", "mcp_servers.octen.startup_timeout_sec=20",
        "-c", "mcp_servers.octen.tool_timeout_sec=30",
        "-c", 'mcp_servers.octen.default_tools_approval_mode="approve"',  # no human to click approve
        "-c", f"model_reasoning_effort={toml(CODEX_REASONING)}",
        # Only Octen counts as search: keep Codex's own web tools off.
        "-c", 'web_search="disabled"',
        "-c", "tools.web_search=false",
        "-c", "features.browser_use=false",
        "-c", "features.computer_use=false",
        "-c", "features.apps=false",
    ]
    if CODEX_MODEL:
        cmd += ["--model", CODEX_MODEL]
    cmd.append("-")  # prompt from stdin
    return cmd


def claude_env() -> dict:
    """The environment of a plain terminal: without what a parent Claude session sets, which changes the CLI's login and mode."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE") and k != "ANTHROPIC_BASE_URL"}
    env["MCP_CONNECTION_NONBLOCKING"] = "false"  # wait for the Octen tool to connect; otherwise Claude answers before it can search
    return env


def claude_command(system: str, schema: str, run_log: str | None = None) -> list[str]:
    """`claude -p` answering in the given JSON schema. With run_log it gets one tool, Octen News Search; without, none."""
    servers = {"octen": {"command": sys.executable, "args": [str(MCP_FILE)], "env": {"SCOOP_RUN_LOG": run_log}}} if run_log else {}
    cmd = [
        CLAUDE, "-p",
        "--model", CLAUDE_MODEL,
        "--system-prompt", system,
        "--output-format", "json",
        "--json-schema", schema,
        "--mcp-config", json.dumps({"mcpServers": servers}),
        "--strict-mcp-config",  # no other MCP servers
        "--tools", "",  # Only Octen counts as search: no built-in WebSearch, WebFetch or Bash
        "--setting-sources", "",  # your personal Claude settings can't add other tools
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if CLAUDE_EFFORT:
        cmd += ["--effort", CLAUDE_EFFORT]
    if run_log:
        cmd += ["--allowedTools", "mcp__octen__news_search"]  # no human to click approve
    return cmd  # the prompt goes in on stdin


def run_claude(cmd: list[str], prompt: str, timeout: float, workdir: str, verbose: bool = False) -> tuple[dict | None, str]:
    """Run `claude -p` and return (its JSON answer, error). Never raises."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=workdir,
            env=claude_env(),
            start_new_session=True,
        )
    except FileNotFoundError:
        return None, "claude not found. Install it: npm install -g @anthropic-ai/claude-code"
    try:
        out, err = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill(proc)
        return None, f"timed out after {timeout:.0f}s"
    if verbose:
        print((out or "").strip()[:4000], (err or "").strip()[-1000:], sep="\n")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None, f"claude exited {proc.returncode}: {(err or out or '').strip()[-300:]}"
    if not isinstance(data, dict) or data.get("is_error"):
        why = (data.get("result") or data.get("subtype") or data.get("terminal_reason") or "failed") if isinstance(data, dict) else data
        return None, f"claude: {str(why)[:300]}"
    answer = data.get("structured_output")
    if not isinstance(answer, dict):  # no structured output: the answer may be JSON in the text
        found = re.search(r"\{.*\}", data.get("result") or "", re.S)
        try:
            answer = json.loads(found.group(0)) if found else None
        except json.JSONDecodeError:
            answer = None
    return (answer, "") if isinstance(answer, dict) else (None, "agent's final answer was not a JSON card")


def run_codex(clue: str, workdir: str, run_log: str, timeout: float, verbose: bool = False) -> tuple[dict | None, str]:
    """Run `codex exec` on one clue and return (its JSON answer, error). Never raises."""
    out_file = os.path.join(workdir, "card.json")
    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip() + f"\n\nClue: {clue}\n"
    error = ""
    try:
        proc = subprocess.Popen(
            codex_command(workdir, out_file, run_log),
            stdin=subprocess.PIPE,
            stdout=None if verbose else subprocess.DEVNULL,
            stderr=None if verbose else subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except FileNotFoundError:
        return None, "codex not found. Install it: npm install -g @openai/codex"
    try:
        _, err = proc.communicate(prompt, timeout=timeout)
        if proc.returncode != 0:
            error = f"codex exited {proc.returncode}: {(err or '').strip()[-300:]}"
    except subprocess.TimeoutExpired:
        kill(proc)
        error = f"timed out after {timeout:.0f}s"
    if not os.path.exists(out_file):
        return None, error
    try:
        data = json.loads(Path(out_file).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = None
    return (data, error) if isinstance(data, dict) else (None, error or "agent's final answer was not a JSON card")


# One call each: the 2-call budget. The first names every language except Chinese and Indonesian: with no filter,
# News Search answers an English clue almost only in English and never shows a story that exists only in, say, Japanese.
SEARCH_LANES = [
    ("any_language", ["en", "ar", "de", "es", "fr", "hi", "it", "ja", "ko", "nl", "pl", "pt", "ru", "th", "tr", "vi"]),
    ("chinese_or_indonesian", ["zh", "id"]),
]
# What Claude answers with: the card, after a check of each clue detail against the article (this is what catches fakes).
PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "description": "Each concrete detail of the clue, checked against the chosen article",
            "items": {
                "type": "object",
                "properties": {"detail": {"type": "string"}, "article_says": {"type": "string"}, "verdict": {"type": "string", "enum": ["same", "different", "not stated"]}},
                "required": ["detail", "article_says", "verdict"],
                "additionalProperties": False,
            },
        },
        "url": {"type": "string", "description": "URL of the article, or empty when any check is 'different' or no article reports the story"},
        "summary": {"type": "string", "description": "One-sentence English summary"},
    },
    "required": ["checks", "url", "summary"],
    "additionalProperties": False,
}

# The answer pairs every detail of the clue with what the articles state, before the url: this is what catches fakes.
PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "story": {
            "type": "string",
            "description": "One English sentence, at most 35 words: what the closest story in the results reports, with real names and its own numbers, places, dates and outcomes. If nothing is close, say so."
        },
        "checks": {
            "type": "array",
            "description": "One entry per concrete detail of the clue",
            "items": {
                "type": "object",
                "properties": {
                    "clue": {
                        "type": "string",
                        "description": "the detail as the clue states it, a few words"
                    },
                    "articles": {
                        "type": "string",
                        "description": "what the articles state for it, a few words, or 'not stated'"
                    },
                    "same": {
                        "type": "boolean",
                        "description": "False only when the articles state it differently"
                    }
                },
                "required": [
                    "clue",
                    "articles",
                    "same"
                ],
                "additionalProperties": False
            }
        },
        "url": {
            "type": "string",
            "description": "URL of one article that reports the clue's story; empty when any check has same=False or no article reports it"
        }
    },
    "required": [
        "story",
        "checks",
        "url"
    ],
    "additionalProperties": False
}


def search_lanes(clue: str, started: float) -> list[dict]:
    """Search with the clue itself, once per lane, both at once.

    News Search matches meaning across languages, so the English clue finds the Chinese or Indonesian article
    when the language filter is set. Call inside octen_client.capture(): each thread runs in a copy of the
    caller's context so its search is recorded.
    """

    def one(lane: tuple[str, list[str] | None]) -> dict:
        name, language = lane
        body = {"query": clue[:500], "count": 10, "time_range": "day", "highlight": {"enable": True, "max_tokens": 200}, "subjects": {"enable": False}}
        if language:
            body["language"] = language
        data = octen_client.news_search(body)
        articles = [
            {"title": a.get("title"), "url": a.get("url"), "published": a.get("time_published"), "text": (a.get("highlight") or "")[:900]}
            for a in (data.get("data") or {}).get("results") or []
        ]
        return {
            "name": name, "articles": articles, "t": round(time.time() - started, 1), "ok": data.get("code", 0) == 0,
            "refused": data.get("code") == 429, "q": f"{clue[:60]} [{', '.join(language) if language else 'any language'}]",
        }

    with ThreadPoolExecutor(len(SEARCH_LANES)) as ex:
        return [f.result() for f in [ex.submit(contextvars.copy_context().run, one, lane) for lane in SEARCH_LANES]]


def run_agent(clue: str, timeout: float = GAME_TIMEOUT, verbose: bool = False) -> Card:
    """Run the agent (Claude, or Codex with SCOOP_AGENT=codex) on one clue and return its card. Never raises."""
    started = time.time()
    workdir = tempfile.mkdtemp(prefix="scoop-")  # empty dir: no AGENTS.md, nothing to read
    run_log = os.path.join(workdir, "searches.jsonl")
    card = Card()
    if AGENT == "claude":
        with octen_client.capture() as searches:
            lanes = search_lanes(clue, started)
        card.searches = list(searches)
        card.search_times = [{"t": s["t"], "ok": s["ok"], "refused": s["refused"], "q": s["q"]} for s in lanes]
        card.refused = sum(s["refused"] for s in lanes)
        results = {s["name"]: s["articles"] for s in lanes}
        prompt = f"now: {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"
        cmd = claude_command(PROMPT_FILE.read_text(encoding="utf-8").strip(), json.dumps(PICK_SCHEMA))
        answer, card.error = run_claude(cmd, prompt, max(1.0, timeout - (time.time() - started)), workdir, verbose)
        if answer is None and timeout - (time.time() - started) > 15:  # a failed call (rate limit, network): one more try if there's time
            answer, card.error = run_claude(cmd, prompt, timeout - (time.time() - started), workdir, verbose)
    else:
        answer, card.error = run_codex(clue, workdir, run_log, timeout, verbose)
    card.seconds = time.time() - started
    if os.path.exists(run_log):  # Codex's searches, logged by its MCP server
        with open(run_log, encoding="utf-8") as f:
            calls = [json.loads(line) for line in f if line.strip()]
        card.searches = [octen_client.label(c.get("api", "news-search"), c["query"]) for c in calls if c.get("status") == 200 and not c.get("code")]
        card.search_times = [{"t": round(c["t"] - started, 1), "ok": c.get("status") == 200 and not c.get("code"), "refused": c.get("status") == 429 or c.get("code") == 429, "q": c["query"][:80]} for c in calls]
        card.refused = sum(c.get("status") == 429 for c in calls)
    if answer:
        card.url = str(answer.get("url") or "").strip()
        card.summary = str(answer.get("summary") or answer.get("story") or "").strip()
        if AGENT == "claude":  # decided in code, not left to the model
            differs = any(isinstance(c, dict) and c.get("same") is False for c in answer.get("checks") or [])
            if differs or card.url not in {a["url"] for s in lanes for a in s["articles"]}:
                card.url = ""  # a detail of the clue differs from the story (a fake), or the link is not a search result
    shutil.rmtree(workdir, ignore_errors=True)
    return card


def kill(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)  # codex and its MCP server
        elif os.name == "nt":  # Windows: kill codex.cmd and everything under it (codex, its MCP server)
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def print_card(card: Card) -> None:
    print(f"  url:      {card.url or '(none)'}")
    print(f"  summary:  {card.summary or '(none)'}")
    print(f"  searches: {len(card.searches)}  {'; '.join(card.searches)[:150]}")
    print(f"  time:     {card.seconds:.1f}s" + (f"   error: {card.error}" if card.error else ""))


# --- answer checking (practice) ------------------------------------------------------
def normalize_url(url: str) -> str:
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


def key_check(key: dict, url: str, langs: list[str] | None = None) -> tuple[bool, bool]:
    """(in the answer key, first report). First report needs 3+ articles in the key.

    With langs (clues that require an article in a given language), key articles in other languages don't count.
    """
    arts = [a for a in (key or {}).get("articles") or [] if not (langs and a.get("language") and a["language"] not in langs)]
    times = {normalize_url(a["url"]): a.get("time_published") or "" for a in arts}
    earliest = min((t for t in times.values() if t), default=None)
    norm = normalize_url(url)
    in_key = norm in times
    return in_key, in_key and len(times) >= 3 and earliest is not None and times[norm] == earliest


def news_searched(searches: list[str]) -> bool:
    """A card scores only if at least one of its searches was a News Search call."""
    return any(octen_client.is_news(q) for q in searches)


def game_points(verdict: str, first: bool, secs: float, searched: bool, house: dict | None, fake: bool = False) -> int | None:
    """Points this card would score in the Scoop clock. None when unknown (judge unsure)."""
    if not searched:
        return 0
    if fake:
        return 2 if verdict == "right" else -2
    if verdict == "blank":
        return 0
    if verdict == "wrong":
        return -2
    if verdict != "right":
        return None
    if house is None:  # house result unknown: assume it got it, and you were slower
        pts = 1
    elif not house.get("right"):
        pts = 4
    else:
        pts = 2 if secs < (house.get("secs") or 999) else 1
    return pts + (2 if first else 0)


# --- commands ---------------------------------------------------------------------
def cmd_doctor(args) -> None:
    ok = True

    def line(good: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else '!!'}] {label}" + (f"  {detail}" if detail else ""))

    print("Scoop Wars doctor")
    line(sys.version_info >= (3, 10), "Python", sys.version.split()[0])
    if AGENT == "claude":
        claude = shutil.which(CLAUDE) or (CLAUDE if os.path.exists(CLAUDE) else None)
        line(bool(claude), "Claude CLI", claude or "not found: npm install -g @anthropic-ai/claude-code")
        if claude:
            r = subprocess.run([CLAUDE, "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=claude_env())
            try:
                logged_in = bool(json.loads(r.stdout).get("loggedIn"))
            except (json.JSONDecodeError, AttributeError):
                logged_in = False
            line(logged_in, "Claude login", f"signed in, model {CLAUDE_MODEL}" if logged_in else "run: claude auth login")
    else:
        codex = shutil.which(CODEX) or (CODEX if os.path.exists(CODEX) else None)
        line(bool(codex), "Codex CLI", codex or "not found: npm install -g @openai/codex")
        if codex:
            r = subprocess.run([CODEX, "login", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace")
            status = (r.stdout + r.stderr).strip().splitlines()
            line(r.returncode == 0, "Codex login", status[-1] if status else "run: codex login")
    try:
        import mcp  # noqa: F401
        import httpx  # noqa: F401

        line(True, "Python packages")
    except ImportError as e:
        line(False, "Python packages", f"{e}. Run with: uv run play.py doctor")
    key = os.environ.get("OCTEN_API_KEY", "")
    line(bool(key), "OCTEN_API_KEY", "set in .env" if key else "missing: add it to .env")
    base = server_url()
    line(bool(base), "SCOOP_SERVER", base or "missing: add the URL from the big screen to .env")
    if base:
        try:
            api("GET", "/healthz", timeout=5)
            line(True, "Game server reachable")
        except ServerError as e:
            line(False, "Game server reachable", str(e))
    t = team()
    if t.get("token") and base:
        try:
            me = api("GET", "/api/team", timeout=5)
            line(True, "Team", f"{me.get('name')} ({', '.join(me.get('members') or []) or 'no members listed'})")
        except ServerError as e:
            line(False, "Team", str(e))
    else:
        line(bool(t.get("token")), "Team", t.get("name") or "missing: copy the SCOOP_TOKEN line from your event page into .env")
    if key:
        sys.path.insert(0, str(KIT_DIR))
        try:
            import news_search_mcp

            res = json.loads(news_search_mcp.news_search("Singapore", count=1))
            line("error" not in res, "News Search call", res.get("error", "works" + (" (through the game server)" if t.get("token") and base else " (direct)")))
        except Exception as e:  # noqa: BLE001
            line(False, "News Search call", str(e))
    print("\nAll good. Next: uv run play.py ui" if ok else "\nFix the [!!] lines, then run doctor again.")
    if not ok:
        sys.exit(1)


def cmd_join(args) -> None:
    base = server_url()
    if not base:
        sys.exit("Set SCOOP_SERVER in .env first (the URL on the big screen).")
    if team().get("token") and not args.force:
        sys.exit(f"Already on a team ({team_name()}). Your team comes from the event page; use --force only if the help desk asks.")
    try:
        res = api("POST", "/api/join", {"name": args.name, "house": args.house}, admin=args.house)
    except ServerError as e:
        sys.exit(f"Join failed: {e}")
    TEAM_FILE.write_text(json.dumps({"name": res["name"], "token": res["token"], "server": base}, indent=2), encoding="utf-8")
    print(f"Joined as '{res['name']}'. Next: uv run play.py eval")


def cmd_ask(args) -> None:
    print(f"Clue: {args.clue}\n")
    card = run_agent(args.clue, timeout=args.timeout, verbose=args.verbose)
    print_card(card)


def load_practice(args) -> list[dict]:
    if args.file:  # organizers: a clues.json with answer keys
        doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
        clues = doc.get("practice", []) + (doc.get("game", []) if args.all else [])
        for i, c in enumerate(clues):
            c.setdefault("id", f"g{c.get('n', i + 1)}")
        return clues
    try:
        return api("GET", "/api/practice")
    except ServerError as e:
        sys.exit(f"Can't load practice clues: {e}")


def judge_card(clue: dict, card: Card) -> dict:
    """Judge one card the way the game does: answer key first, then the judge."""
    if clue.get("fake"):
        if card.url:
            return {"verdict": "wrong", "first": False, "reason": "Fake clue: this event didn't happen, so any link is wrong."}
        return {"verdict": "right", "first": False, "reason": "Fake clue, answered blank."}
    if not card.url:
        return {"verdict": "blank", "first": False, "reason": card.error or "no answer"}
    langs = clue.get("source_language")
    in_key, first = key_check(clue.get("key"), card.url, langs)
    if in_key:
        return {"verdict": "right", "first": first, "reason": "in the answer key", "in_key": True}
    sys.path.insert(0, str(KIT_DIR))
    import judge

    run_judge = claude_judge if AGENT == "claude" else judge.judge
    r = run_judge(clue["text"], clue.get("key") or {}, card.url, card.summary, require_language=langs)
    verdict = {"match": "right", "no_match": "wrong"}.get(r["verdict"], "unsure")
    return {"verdict": verdict, "first": False, "reason": r["reason"]}


def claude_judge(clue: str, key: dict, url: str, summary: str, require_language: list[str] | None = None, timeout: float = 90) -> dict:
    """judge.judge() run by Claude instead of Codex: the same evidence and prompt, so eval needs no Codex login.

    The game server still judges with Codex, so a borderline link can come out differently there.
    """
    import judge

    page = judge.fetch_page_meta(url)
    if not page.get("published"):
        page = {**page, **judge.lookup_in_octen(url, summary)}
    prompt = judge.build_prompt(clue, key, url, summary, time.time(), page, require_language)
    workdir = tempfile.mkdtemp(prefix="scoop-judge-")
    cmd = claude_command("You are a careful judge. Decide only from the evidence in the message.", json.dumps(judge.SCHEMA))
    answer, error = run_claude(cmd, prompt, timeout, workdir)
    shutil.rmtree(workdir, ignore_errors=True)
    if answer and answer.get("verdict") in (judge.MATCH, judge.NO_MATCH, judge.UNSURE):
        return {"verdict": answer["verdict"], "reason": str(answer.get("reason") or "").strip()[:300]}
    return {"verdict": judge.UNSURE, "reason": f"Judge failed to run: {error}" if error else "Judge failed to run."}


def cmd_eval(args) -> None:
    clues = load_practice(args)
    if args.only:
        clues = [c for c in clues if c["id"] in args.only.split(",")]
    if not clues:
        sys.exit("No practice clues yet.")
    print(f"Running {len(clues)} clues, {args.parallel} at a time (times run a little slower in parallel)...\n")
    started = time.time()
    rows: dict[str, dict] = {}
    progress = Progress(clues, args.parallel)

    def one(c: dict) -> dict:
        progress.update(c["id"], status="running", started=time.time())
        card = run_agent(c["text"], timeout=args.timeout, verbose=args.verbose)
        progress.update(c["id"], status="judging", secs=round(card.seconds, 1), url=card.url, search_times=card.search_times)
        j = judge_card(c, card)
        pts = game_points(j["verdict"], j["first"], card.seconds, news_searched(card.searches), c.get("house"), bool(c.get("fake")))
        return {
            "id": c["id"], "clue": c["text"], "verdict": j["verdict"], "first": j["first"], "points": pts,
            "secs": round(card.seconds, 1), "searches": card.searches, "url": card.url, "summary": card.summary,
            "reason": j["reason"], "error": card.error, "house": c.get("house"), "story": (c.get("key") or {}).get("story"), "key_size": len((c.get("key") or {}).get("articles") or []), "key": [{"url": k.get("url"), "title": k.get("title"), "time": k.get("time_published")} for k in ((c.get("key") or {}).get("articles") or [])[:6]], "in_key": bool(j.get("in_key")),
            "refused": card.refused, "fake": bool(c.get("fake")), "search_times": card.search_times,
        }

    with ThreadPoolExecutor(max(1, args.parallel)) as ex:
        futures = {ex.submit(one, c): c for c in clues}
        for f in as_completed(futures):
            r = f.result()
            rows[r["id"]] = r
            progress.update(r["id"], status="done", **r)
            mark = {"right": "RIGHT", "wrong": "WRONG", "blank": "blank", "unsure": "unsure"}[r["verdict"]]
            print(f"  [{r['id']}] {mark:<6} {r['secs']:>5.1f}s  {len(r['searches'])} searches  {r['url'][:70] if r['url'] else ''}")
    ordered = [rows[c["id"]] for c in clues]
    print_eval(ordered, verbose=args.verbose)

    RUNS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RUNS_DIR / f"eval-{stamp}.json"
    previous = sorted(RUNS_DIR.glob("eval-*.json"))
    out.write_text(json.dumps({"at": stamp, "secs": round(time.time() - started), "summary": summarize(ordered), "rows": ordered}, indent=2, ensure_ascii=False), encoding="utf-8")
    progress.finish(out.name)
    print(f"\nSaved {out.relative_to(KIT_DIR)}")
    if args.compare and previous:
        compare_runs(json.loads(previous[-1].read_text(encoding="utf-8"))["rows"], ordered, previous[-1].name)
    if args.record_house:
        record_house(args.file, ordered)


class Progress:
    """Writes runs/current.json while an eval runs, for `play.py ui`."""

    def __init__(self, clues: list[dict], parallel: int):
        RUNS_DIR.mkdir(exist_ok=True)
        self.lock = threading.Lock()
        self.path = RUNS_DIR / "current.json"
        self.state = {
            "status": "running", "started": time.time(), "parallel": parallel, "saved_as": None,
            "rows": [{"id": c["id"], "clue": c["text"], "status": "queued", "house": c.get("house"), "fake": bool(c.get("fake"))} for c in clues],
        }
        self._write()

    def update(self, cid: str, **fields) -> None:
        with self.lock:
            for row in self.state["rows"]:
                if row["id"] == cid:
                    row.update(fields)
            self._write()

    def finish(self, saved_as: str) -> None:
        with self.lock:
            self.state.update(status="done", saved_as=saved_as, finished=time.time())
            self._write()

    def _write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    known = [r for r in rows if r.get("house") is not None]
    missed = [r for r in known if not r["house"].get("right")]
    secs = sorted(r["secs"] for r in rows) or [0]
    return {
        "n": n,
        "right": sum(r["verdict"] == "right" for r in rows),
        "points": sum(r["points"] or 0 for r in rows),
        "house_missed": len(missed),
        "beat_house": sum(r["verdict"] == "right" for r in missed),
        "median_secs": secs[len(secs) // 2],
        "timeouts": sum("timed out" in (r.get("error") or "") for r in rows),
    }


def print_eval(rows: list[dict], verbose: bool = False) -> None:
    print(f"\n{'clue':<6} {'result':<8} {'pts':>4} {'time':>6} {'srch':>4}  house   clue text")
    for r in rows:
        h = r["house"]
        house = "?" if h is None else (f"{h['secs']:.0f}s" if h.get("right") else "missed")
        pts = "?" if r["points"] is None else f"{r['points']:+d}"
        res = r["verdict"] + ("*" if r["first"] else "")
        srch = f"{len(r['searches'])}" + ("+" if r.get("refused") else "")
        print(f"{r['id']:<6} {res:<8} {pts:>4} {r['secs']:>5.1f}s {srch:>4}  {house:<7} {r['clue'][:60]}")
        if verbose or r["verdict"] in ("wrong", "unsure"):
            print(f"{'':<6} {r['reason'][:120]}")
    n = len(rows)
    right = sum(r["verdict"] == "right" for r in rows)
    pts = sum(r["points"] or 0 for r in rows)
    known = [r for r in rows if r["house"] is not None]
    missed = [r for r in known if not r["house"].get("right")]
    beat = sum(r["verdict"] == "right" for r in missed)
    secs = sorted(r["secs"] for r in rows)
    timeouts = sum("timed out" in (r["error"] or "") for r in rows)
    nosearch = sum(not news_searched(r["searches"]) and (r["url"] or r.get("fake")) for r in rows)
    refused = sum(1 for r in rows if r.get("refused"))
    print(f"\nRight {right}/{n}   Estimated game points {pts:+d}   Median {secs[n // 2]:.0f}s, slowest {secs[-1]:.0f}s")
    if known:
        print(f"Clues the house agent missed: you got {beat}/{len(missed)} (4 points each)")
    if timeouts:
        print(f"Timed out: {timeouts} (the game stops your agent at {GAME_TIMEOUT}s)")
    if nosearch:
        print(f"Answered without a News Search call: {nosearch} (scores 0 in the game)")
    if refused:
        print(f"Hit the search budget ({os.environ.get('SCOOP_SEARCH_BUDGET', '2')} per clue): {refused} clue(s)")
    print("* first report: +2   srch+ = hit the search budget")
    if any(r["verdict"] == "unsure" for r in rows):
        print("unsure = link not in the answer key and the judge couldn't confirm it: not counted here; in the game, the game master decides")


def compare_runs(before: list[dict], after: list[dict], name: str) -> None:
    old = {r["id"]: r for r in before}
    print(f"\nCompared with {name}:")
    changed = 0
    for r in after:
        o = old.get(r["id"])
        if not o:
            continue
        if o["verdict"] != r["verdict"] or (o["points"] or 0) != (r["points"] or 0):
            changed += 1
            print(f"  {r['id']:<6} {o['verdict']:>6} {o['points'] if o['points'] is not None else '?':>3} -> {r['verdict']:<6} {r['points'] if r['points'] is not None else '?':>3}   ({o['secs']:.0f}s -> {r['secs']:.0f}s)")
    tot = lambda rows: sum(r["points"] or 0 for r in rows if r["id"] in old)  # noqa: E731
    med = lambda rows: sorted(x["secs"] for x in rows)[len(rows) // 2] if rows else 0  # noqa: E731
    print(f"  {changed} clue(s) changed. Points {tot(before):+d} -> {tot(after):+d}. Median time {med(before):.0f}s -> {med(after):.0f}s")


def record_house(path: str | None, rows: list[dict]) -> None:
    """Organizers: write the house agent's eval results into the clues file."""
    if not path:
        sys.exit("--record-house needs --file pointing at the server's clues.json")
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in rows}
    n = 0
    for section in ("practice", "game"):
        for i, c in enumerate(doc.get(section, []), start=1):
            cid = c.get("id") or f"g{c.get('n', i)}"
            r = by_id.get(cid)
            if r and r["verdict"] in ("right", "wrong", "blank"):
                c["house"] = {"right": r["verdict"] == "right" and news_searched(r["searches"]), "secs": r["secs"]}
                n += 1
    Path(path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Recorded house results for {n} clues in {path}. Reload clues in the console.")


def cmd_ui(args) -> None:
    sys.path.insert(0, str(KIT_DIR))
    import eval_ui

    eval_ui.serve(args.port, open_browser=not args.no_browser)


def cmd_play(args) -> None:
    if not team().get("token"):
        sys.exit("No team yet: copy the SCOOP_TOKEN line from your event page into .env")
    try:
        api("GET", "/api/team", timeout=10)
    except ServerError as e:
        if str(e).startswith("401"):
            sys.exit(f"{e}\nFix SCOOP_TOKEN in .env, then run play again.")
        print(f"Can't check your team yet ({e}). Carrying on: play keeps retrying the game server.")
    print(f"Playing as '{team_name()}' on {server_url()}. Hands off the keyboard once the clock starts.\n")
    attempted: set[int] = set()
    worker: threading.Thread | None = None
    last_msg = ""

    def answer(n: int, text: str, budget: float) -> None:
        print(f"\n=== Clue {n}: {text}")
        card = run_agent(text, timeout=budget)
        print_card(card)
        for attempt in range(3):  # venue Wi-Fi: don't lose a card to one dropped request
            try:
                api("POST", "/api/cards", {"n": n, "url": card.url, "summary": card.summary}, timeout=5)
                print(f"  submitted clue {n}")
                return
            except ServerError as e:
                if attempt == 2 or str(e)[:1] == "4":  # the server said no (closed, already answered): retrying won't help
                    print(f"  NOT submitted: {e}. Paste the card into the backup form.")
                    return
                print(f"  submit failed ({e}), retrying...")
                time.sleep(1)

    while True:
        try:
            st = api("GET", "/api/state", timeout=5)
        except ServerError as e:
            msg = f"[server] {e}. Retrying..."
            if msg != last_msg:
                print(msg)
                last_msg = msg
            time.sleep(2)
            continue
        busy = worker is not None and worker.is_alive()
        if st["phase"] == "open" and st["clue"] and not st["answered"] and st["clue"]["n"] not in attempted and not busy:
            n = st["clue"]["n"]
            attempted.add(n)
            budget = max(5.0, (st["closes_in"] or 45) - 4)
            worker = threading.Thread(target=answer, args=(n, st["clue"]["text"], budget), daemon=True)
            worker.start()
        elif st["phase"] == "done" and not busy:
            print("\nScoop clock finished. Watch the big screen.")
            return
        elif st["phase"] == "lobby":
            msg = "Connected. Waiting for the Scoop clock to start" + (f" (in {st['next_in']:.0f}s)" if st.get("next_in") else "") + "..."
            if msg.split(" (")[0] != last_msg.split(" (")[0]:
                print(msg)
            last_msg = msg
        time.sleep(1)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check your setup").set_defaults(fn=cmd_doctor)
    j = sub.add_parser("join", help="register a team (only if the help desk asks)")
    j.add_argument("name")
    j.add_argument("--force", action="store_true", help="join again as a new team")
    j.add_argument("--house", action="store_true", help=argparse.SUPPRESS)
    j.set_defaults(fn=cmd_join)
    a = sub.add_parser("ask", help="run the agent on one clue")
    a.add_argument("clue")
    a.add_argument("--timeout", type=float, default=GAME_TIMEOUT)
    a.add_argument("-v", "--verbose", action="store_true", help="show Codex's output")
    a.set_defaults(fn=cmd_ask)
    for name in ("eval",):
        ev = sub.add_parser(name, aliases=["practice"], help="score the agent on the practice clues")
        ev.add_argument("--only", help="comma-separated clue ids")
        ev.add_argument("--parallel", type=int, default=4, help="clues to run at once (default 4)")
        ev.add_argument("--compare", action="store_true", help="show what changed since the last eval")
        ev.add_argument("--timeout", type=float, default=GAME_TIMEOUT)
        ev.add_argument("-v", "--verbose", action="store_true", help="show every judge reason")
        ev.add_argument("--file", help=argparse.SUPPRESS)  # organizers: a clues.json with answer keys
        ev.add_argument("--all", action="store_true", help=argparse.SUPPRESS)  # with --file: game clues too
        ev.add_argument("--record-house", action="store_true", help=argparse.SUPPRESS)  # organizers
        ev.set_defaults(fn=cmd_eval)
    u = sub.add_parser("ui", help="eval dashboard in your browser")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true")
    u.set_defaults(fn=cmd_ui)
    sub.add_parser("play", help="play the Scoop clock").set_defaults(fn=cmd_play)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
