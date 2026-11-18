"""Harvest today's real stories from Octen News Search, as raw material for practice clues.

    uv run python lab/harvest.py

Writes lab/stories.json: one record per story, with the articles that report it (the answer key).
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_DIR))
sys.path.insert(0, str(LAB_DIR.parent))

import octen_cached  # noqa: E402
import play  # noqa: E402

SEEDS = {
    "en": [
        "government announced a new policy today", "court ruling verdict sentenced", "police arrested a suspect",
        "accident crash injured people", "company quarterly results shares", "central bank inflation economic data",
        "football match result", "tournament final win sport", "scientists study finds", "health ministry disease outbreak",
        "technology company launches AI", "storm flood weather warning", "union workers strike vote", "election parliament vote",
        "airline airport transport disruption", "museum auction art culture", "school university students", "military defence deal",
        "oil gas energy prices", "city council local residents", "regulator fine investigation", "wildlife environment conservation",
    ],
    "zh": [
        "政府宣布新政策", "法院判决 判刑", "警方逮捕嫌犯", "车祸事故 受伤送医", "公司财报 股价", "央行 通胀 经济数据",
        "足球比赛 赛果", "研究发现 科学家", "卫生部门 疫情", "科技公司 人工智能", "暴雨 台风 天气", "罢工 工会", "选举 议会 立法",
        "机场 航空 交通", "学校 学生 教育", "新加坡 今日新闻", "香港 突發 新聞", "台灣 社會 新聞", "马来西亚 今日新闻", "監管 罰款 調查",
    ],
    "id": [
        "pemerintah mengumumkan kebijakan baru", "pengadilan menjatuhkan vonis", "polisi menangkap tersangka",
        "kecelakaan korban luka", "laba perusahaan saham", "Bank Indonesia rupiah inflasi", "hasil pertandingan sepak bola",
        "penelitian menemukan", "kementerian kesehatan wabah", "teknologi kecerdasan buatan", "banjir cuaca ekstrem",
        "buruh mogok kerja", "DPR pemilu rapat", "bandara penerbangan transportasi", "sekolah siswa pendidikan",
        "KPK korupsi", "harga BBM pangan", "pemerintah daerah warga",
    ],
}
OTHER_LANGUAGES = ["ja", "ko", "es", "fr", "de", "th", "vi", "ar", "hi", "pt", "tr", "ru"]
OTHER_SEEDS = ["the government announced a new policy today", "police court accident investigation"]


def article(a: dict) -> dict:
    return {"url": a.get("url"), "title": a.get("title"), "time_published": a.get("time_published")}


def harvest_one(job: tuple[str, str]) -> list[dict]:
    language, seed = job
    body = {
        "query": seed, "count": 50, "time_range": "day", "language": [language],
        "highlight": {"enable": True, "max_tokens": 160},
        "subjects": {"enable": True, "count": 5, "max_sub_news": 10},
    }
    data = (octen_cached.search(body).get("data")) or {}
    stories = []
    clustered: dict[str, dict] = {}
    for s in data.get("subjects") or []:
        arts = [article(a) for a in [s.get("top_news") or {}] + list(s.get("sub_news") or []) if a.get("url")]
        for a in arts:
            clustered[play.normalize_url(a["url"])] = {"name": s.get("name"), "summary": s.get("summary"), "started": s.get("time_start"), "articles": arts}
    for a in data.get("results") or []:
        if not a.get("url") or not a.get("title"):
            continue
        cluster = clustered.get(play.normalize_url(a["url"]))
        stories.append({
            "language": language, "seed": seed, "title": a["title"], "url": a["url"], "time_published": a.get("time_published"),
            "text": (a.get("highlight") or "").strip(),
            "story": (cluster or {}).get("name"), "summary": (cluster or {}).get("summary"), "started": (cluster or {}).get("started"),
            "articles": (cluster or {}).get("articles") or [article(a)],
        })
    return stories


def main() -> None:
    jobs = [(lang, seed) for lang, seeds in SEEDS.items() for seed in seeds] + [(lang, seed) for lang in OTHER_LANGUAGES for seed in OTHER_SEEDS]
    with ThreadPoolExecutor(6) as ex:
        found = [s for batch in ex.map(harvest_one, jobs) for s in batch]
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    stories = []
    for s in found:
        urls = {play.normalize_url(a["url"]) for a in s["articles"]} | {play.normalize_url(s["url"])}
        title = re.sub(r"\W+", "", s["title"].lower())[:40]
        if urls & seen_urls or title in seen_titles or len(s["text"]) < 180:
            continue  # the same story again, or too little detail to write a clue from
        seen_urls |= urls
        seen_titles.add(title)
        for a in s["articles"]:
            a["language"] = s["language"]
        s["sid"] = f"s{len(stories) + 1:04d}"
        stories.append(s)
    (LAB_DIR / "stories.json").write_text(json.dumps(stories, ensure_ascii=False, indent=1), encoding="utf-8")
    by_lang: dict[str, int] = {}
    for s in stories:
        by_lang[s["language"]] = by_lang.get(s["language"], 0) + 1
    multi = sum(len(s["articles"]) > 1 for s in stories)
    print(f"{len(found)} articles -> {len(stories)} distinct stories ({multi} with more than one article in the key)")
    print("by language:", dict(sorted(by_lang.items(), key=lambda kv: -kv[1])))
    print("live calls so far:", octen_cached.spend()["live_calls"])


if __name__ == "__main__":
    main()
