"""Octen calls for the lab: cached on disk, throttled, and never through the game server.

Every search in the lab goes through search(). A repeated request body is read from lab/cache/ and costs
nothing, so strategies can be compared on the same results and re-run for free. Live calls are spaced to
LAB_QPS (default 5; the account allows 20 and the live game shares it) and counted in lab/cache/_spend.json,
in total and per LAB_TAG (each investigator has its own tag and allowance, LAB_TAG_MAX).
"""

import fcntl
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_DIR.parent))

import httpx  # noqa: E402
import octen_client  # noqa: E402

CACHE_DIR = LAB_DIR / "cache"
SPEND_FILE = CACHE_DIR / "_spend.json"
QPS = float(os.environ.get("LAB_QPS", "5"))
MAX_LIVE_CALLS = int(os.environ.get("LAB_MAX_CALLS", "5500"))  # the whole study: about USD 16 of News Search
TAG = os.environ.get("LAB_TAG", "main")
TAG_MAX = int(os.environ.get("LAB_TAG_MAX", "100000"))

_lock = threading.Lock()
_next_slot = 0.0


def _key(api: str, body: dict) -> Path:
    digest = hashlib.sha1(json.dumps([api, body], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def spend() -> dict:
    try:
        return json.loads(SPEND_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"live_calls": 0, "tags": {}}


def _reserve_live_call() -> None:
    """Wait for this call's turn and count it. Raises when the study's or this tag's budget is used up."""
    global _next_slot
    CACHE_DIR.mkdir(exist_ok=True)
    with open(CACHE_DIR / "_spend.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # other lab processes count in the same file
        total = spend()
        tags = total.setdefault("tags", {})
        if total["live_calls"] >= MAX_LIVE_CALLS:
            raise RuntimeError(f"Lab search budget used: {MAX_LIVE_CALLS} live calls in all.")
        if tags.get(TAG, 0) >= TAG_MAX:
            raise RuntimeError(f"Search allowance used for {TAG}: {TAG_MAX} live calls. Work from the cache: repeat earlier request bodies.")
        total["live_calls"] += 1
        tags[TAG] = tags.get(TAG, 0) + 1
        tmp = SPEND_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(total), encoding="utf-8")
        tmp.replace(SPEND_FILE)
    with _lock:
        now = time.time()
        wait = max(0.0, _next_slot - now)
        _next_slot = max(now, _next_slot) + 1.0 / QPS
    if wait:
        time.sleep(wait)


def search(body: dict, api: str = "news-search", fresh: bool = False) -> dict:
    """Octen's JSON response for this request, with "_cached" and "_secs" (the latency when it was live) added.

    api is a search API (octen_client.SEARCH_APIS) or "extract" / "embedding", which the game allows outside
    the 2-search budget. Failures come back as {"code": <non-zero>, "msg": ...} and are not cached.
    """
    assert not os.environ.get("SCOOP_TOKEN") and not octen_client._team().get("token"), "the lab must not use the game server"
    CACHE_DIR.mkdir(exist_ok=True)
    path = _key(api, body)
    if path.exists() and not fresh:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_cached"] = True
        return data
    _reserve_live_call()
    started = time.time()
    if api in octen_client.SEARCH_APIS:
        data = octen_client.search(api, body)
    else:
        try:
            r = httpx.post(f"{octen_client.OCTEN_URL}/{api}", json=body, headers={"x-api-key": octen_client._load_env().get("OCTEN_API_KEY", "")}, timeout=30)
            data = r.json()
            if r.status_code != 200 and data.get("code", 0) == 0:
                data["code"] = r.status_code
        except (httpx.HTTPError, ValueError) as e:
            data = {"code": 599, "msg": f"network error: {e}"}
    data["_secs"] = round(time.time() - started, 3)
    if data.get("code", 0) == 0:
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    data["_cached"] = False
    return data
