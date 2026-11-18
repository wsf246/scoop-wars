"""Live timing probes for the claude CLI (not a strategy; run directly, a few calls only).

    uv run python lab/strategies/speed_timing.py <variant> [trials] [clue index]

Each probe runs the picker call the way play.run_claude does (same flags, play.claude_env()), but with
--output-format stream-json --include-partial-messages so every stage gets a timestamp:
  spawn -> init (CLI start-up) -> first token (message_start) -> first text -> result.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(LAB), str(LAB.parent)]
os.environ.setdefault("LAB_TAG", "speed")

import bench  # noqa: E402
import play  # noqa: E402
from strategies import baseline, speed_lib  # noqa: E402

SCHEMA = (LAB.parent / "card.schema.json").read_text(encoding="utf-8")
JSON_TAIL = '\n\nAnswer with only a JSON object, no other text: {"url": "...", "summary": "..."}'


TRIM = {}


def sample_prompt(i: int) -> str:
    clue = bench.load_clues("mini")[i]
    tools = bench.Tools()
    results = baseline.retrieve(clue["text"], tools)  # cached searches
    if TRIM:
        results = speed_lib.trim(results, **TRIM)
    from datetime import datetime, timezone

    # the real clock with seconds, as play.py sends it: a new prompt every time, so no API prompt-cache hit on the results
    return f"now: {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}\nClue: {clue['text']}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"


def command(model: str, effort: str, schema: bool, system: str, stream_in: bool = False) -> list[str]:
    cmd = [play.CLAUDE, "-p", "--model", model, "--system-prompt", system, "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--mcp-config", '{"mcpServers": {}}', "--strict-mcp-config", "--tools", "", "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence"]
    if schema:
        cmd += ["--json-schema", SCHEMA]
    if effort:
        cmd += ["--effort", effort]
    if stream_in:
        cmd += ["--input-format", "stream-json"]
    return cmd


def probe(cmd: list[str], prompt: str, prewarm: float = 0.0, stream_in: bool = False, extra_env: dict | None = None, more_prompts: list[str] | None = None) -> list[dict]:
    """Run one process; returns one timeline per prompt (more_prompts: fed to the same process, stream-json input only)."""
    env = play.claude_env()
    env.update(extra_env or {})
    workdir = tempfile.mkdtemp(prefix="scoop-lab-")
    t_spawn = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", cwd=workdir, env=env, start_new_session=True)
    events: list[tuple[float, dict]] = []
    done = threading.Event()

    def reader() -> None:
        for line in proc.stdout:
            try:
                events.append((time.time(), json.loads(line)))
            except json.JSONDecodeError:
                pass
        done.set()

    threading.Thread(target=reader, daemon=True).start()
    if prewarm:
        time.sleep(prewarm)
    prompts = [prompt] + (more_prompts or [])
    out = []
    for p in prompts:
        seen = len(events)
        t_send = time.time()
        if stream_in:
            proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": p}}, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        else:
            proc.stdin.write(p)
            proc.stdin.close()
        while not any(e.get("type") == "result" for _, e in events[seen:]) and not done.is_set():
            time.sleep(0.01)
        mine = events[seen:] if (stream_in and prewarm) or len(out) else events
        row = {"send_after_spawn": round(t_send - t_spawn, 2)}

        def first(pred) -> float | None:
            return next((round(t - t_send, 2) for t, e in mine if pred(e)), None)

        row["init"] = next((round(t - t_spawn, 2) for t, e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
        starts = [round(t - t_send, 2) for t, e in mine if e.get("type") == "stream_event" and e["event"].get("type") == "message_start"]
        row["message_starts"] = starts
        row["first_delta"] = first(lambda e: e.get("type") == "stream_event" and e["event"].get("type") == "content_block_delta")
        blocks = [e["event"]["content_block"].get("type") + (":" + e["event"]["content_block"].get("name", "") if e["event"]["content_block"].get("type") == "tool_use" else "") for _, e in mine if e.get("type") == "stream_event" and e["event"].get("type") == "content_block_start"]
        row["blocks"] = blocks
        row["block_starts"] = [round(t - t_send, 2) for t, e in mine if e.get("type") == "stream_event" and e["event"].get("type") == "content_block_start"]
        row["message_stop"] = first(lambda e: e.get("type") == "stream_event" and e["event"].get("type") == "message_stop")
        row["result_at"] = first(lambda e: e.get("type") == "result")
        res = next((e for _, e in mine if e.get("type") == "result"), {})
        row.update({k: res.get(k) for k in ("duration_ms", "duration_api_ms", "num_turns", "is_error")})
        u = res.get("usage") or {}
        row["tokens"] = {"in": u.get("input_tokens"), "cache_read": u.get("cache_read_input_tokens"), "cache_write": u.get("cache_creation_input_tokens"), "out": u.get("output_tokens")}
        row["answer"] = res.get("structured_output") or (res.get("result") or "")[:200]
        out.append(row)
    if stream_in:
        proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        play.kill(proc)
    for r in out:
        r["exit_after_send"] = round(time.time() - t_spawn, 2)
    err = proc.stderr.read()
    if err.strip():
        out[0]["stderr"] = err.strip()[-300:]
    return out


VARIANTS = {
    # name: (model, effort, schema, prewarm, stream_in, env)
    "base": ("sonnet", "low", True, 0, False, {}),
    "noschema": ("sonnet", "low", False, 0, False, {}),
    "haiku": ("haiku", "low", True, 0, False, {}),
    "haiku_noschema": ("haiku", "low", False, 0, False, {}),
    "haiku_noeffort": ("haiku", "", True, 0, False, {}),
    "sonnet_noeffort": ("sonnet", "", True, 0, False, {}),
    "nothink": ("sonnet", "low", True, 0, False, {"MAX_THINKING_TOKENS": "0"}),
    "nothink_noschema": ("sonnet", "low", False, 0, False, {"MAX_THINKING_TOKENS": "0"}),
    "haiku_nothink": ("haiku", "low", True, 0, False, {"MAX_THINKING_TOKENS": "0"}),
    "haiku_nothink_noschema": ("haiku", "low", False, 0, False, {"MAX_THINKING_TOKENS": "0"}),
    "short": ("sonnet", "low", True, 0, False, {}),
    "trim5": ("sonnet", "low", True, 0, False, {}),
    "prewarm_long": ("sonnet", "low", True, 45, True, {}),
    "prewarm_text": ("sonnet", "low", True, 6, False, {}),
    "prewarm_stream": ("sonnet", "low", True, 6, True, {}),
    "prewarm_stream_noschema": ("sonnet", "low", False, 6, True, {}),
    "stream_multi": ("sonnet", "low", True, 6, True, {}),
}

if __name__ == "__main__":
    name = sys.argv[1]
    trials = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    model, effort, schema, prewarm, stream_in, env = VARIANTS[name]
    system = (speed_lib.PROMPT_SHORT if name == "short" else baseline.PROMPT) + ("" if schema else JSON_TAIL)
    if name == "trim5":
        TRIM.update(per_lane=5, chars=900)
    for t in range(trials):
        more = [sample_prompt(idx + 1), sample_prompt(idx + 2)] if name == "stream_multi" else None
        for row in probe(command(model, effort, schema, system, stream_in), sample_prompt(idx + t if name != "stream_multi" else idx), prewarm, stream_in, env, more):
            print(name, json.dumps(row, ensure_ascii=False))
