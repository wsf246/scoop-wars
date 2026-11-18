"""Merge the clue writers' parts into lab/clues.json, with answer keys and a dev/test split.

    uv run python lab/build_clues.py

Reads lab/clues/part-*.json (written by the clue writers) and lab/slices/slice-*.json (the stories).
Writes lab/clues.json for bench.py, and lab/clues_practice.json in the format `play.py eval --file` reads.
"""

import collections
import json
import random
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
KEY_SINCE = "2026-11-17T00:00"  # a same-event article older than this is outside the game's 24 hours


def main() -> None:
    stories = {}
    for f in sorted((LAB_DIR / "slices").glob("slice-*.json")):
        for s in json.loads(f.read_text(encoding="utf-8")):
            stories[s["sid"]] = s
    clues, problems, used = [], [], set()
    for f in sorted((LAB_DIR / "clues").glob("part-*.json")):
        for c in json.loads(f.read_text(encoding="utf-8")):
            story = stories.get(c.get("sid"))
            text = " ".join(str(c.get("text") or "").split())
            words = len(text.split())
            if story is None or c["sid"] in used or not 8 <= words <= 70:
                problems.append(f"{f.name}: dropped {c.get('sid')} ({'unknown story' if story is None else 'duplicate story' if c['sid'] in used else f'{words} words'})")
                continue
            used.add(c["sid"])
            fake = bool(c.get("fake")) or c.get("type") == "fake"
            need = c.get("require_language") if c.get("require_language") in ("zh", "id") and story["language"] == c.get("require_language") else None
            kind = "fake" if fake else c.get("type") or "plain"
            group = kind if kind in ("fake", "twin", "minimal", "other_language") else (f"local_{need}" if need else "plain")
            source = {"url": story["url"], "title": story["title"], "time_published": story["time_published"], "language": story["language"]}
            same = [
                {"url": a["url"], "title": a["title"], "time_published": a["time_published"], "language": story["language"]}
                for i, a in enumerate(story["similar_articles"])
                if i in (c.get("same_event") or []) and (a.get("time_published") or "") >= KEY_SINCE
            ]
            clues.append({
                "text": text, "fake": fake, "group": group, "type": kind, "source_language": [need] if need else None,
                "sid": c["sid"], "language": story["language"], "trick": c.get("trick") or "", "changed": c.get("changed") or "",
                "source": {**source, "text": story["text"]},
                "key": {"story": story["title"], "summary": story["text"][:400], "started": story["time_published"], "articles": [] if fake else [source] + same},
            })
    random.seed(20261118)
    random.shuffle(clues)
    by_group = collections.defaultdict(list)
    for c in clues:
        by_group[c["group"]].append(c)
    for group, rows in by_group.items():  # 30% dev (40% of it mini), 70% test, in every group
        n_dev = round(len(rows) * 0.3)
        n_mini = round(n_dev * 0.4)
        for i, c in enumerate(rows):
            c["split"] = "dev" if i < n_dev else "test"
            c["mini"] = i < n_mini
    clues.sort(key=lambda c: (c["split"], c["group"], c["sid"]))
    for i, c in enumerate(clues, start=1):
        c["id"] = f"c{i:03d}"
    (LAB_DIR / "clues.json").write_text(json.dumps(clues, ensure_ascii=False, indent=1), encoding="utf-8")
    practice = [{"id": c["id"], "text": c["text"], "fake": c["fake"], "source_language": c["source_language"], "key": c["key"]} for c in clues]
    (LAB_DIR / "clues_practice.json").write_text(json.dumps({"practice": practice}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(clues)} clues   dev {sum(c['split'] == 'dev' for c in clues)} (mini {sum(c['mini'] for c in clues)})   test {sum(c['split'] == 'test' for c in clues)}")
    print("groups:", dict(sorted(collections.Counter(c["group"] for c in clues).items())))
    print("need zh:", sum(c["source_language"] == ["zh"] for c in clues), " need id:", sum(c["source_language"] == ["id"] for c in clues),
          " keys with more than one article:", sum(len(c["key"]["articles"]) > 1 for c in clues))
    for line in problems[:15]:
        print("  !", line)


if __name__ == "__main__":
    main()
