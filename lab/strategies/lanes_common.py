"""Shared pieces for the `lanes` study: how the two search calls are spent.

A lane is one News Search call with the raw clue as the query (exactly the baseline's body) and a language
filter. Lane names: "any" (no filter), "zh+id", "en", "zh", "id", "ja", ..., "wide" (every language the API
lists except English), "asia" (zh, id, ja, ko, th, vi, hi), "nonen_west" (es, pt, fr, de, it, ...).
"""

import re

ALL_LANGS = ["ar", "de", "en", "es", "fr", "hi", "id", "it", "ja", "ko", "nl", "pl", "pt", "ru", "th", "tr", "vi", "zh"]
LANE_LANGS = {
    "any": None,
    "zh+id": ["zh", "id"],  # the baseline's second lane, same order so the cached bodies match
    "wide": [l for l in ALL_LANGS if l != "en"],
    "rest": [l for l in ALL_LANGS if l not in ("en", "zh", "id")],
}


def lane_language(name: str) -> list | None:
    if name in LANE_LANGS:
        return LANE_LANGS[name]
    return name.split("+")


def body(clue: str, lane: str) -> dict:
    b = {"query": clue[:500], "count": 10, "time_range": "day", "highlight": {"enable": True, "max_tokens": 200}}
    language = lane_language(lane)
    if language:
        b["language"] = language
    return b


# --- language of an article, from its title and text --------------------------------------------------
_STOP = {
    "id": {"yang", "dan", "di", "ke", "dari", "untuk", "dengan", "pada", "ini", "itu", "akan", "tidak", "dalam", "oleh", "juga", "sebagai", "telah", "karena"},
    "es": {"el", "los", "las", "del", "una", "por", "con", "que", "más", "sus", "tras", "según", "fue", "en", "la"},
    "pt": {"os", "das", "dos", "uma", "não", "com", "que", "são", "foi", "pela", "pelo", "após", "mais", "em", "ao", "na", "no"},
    "fr": {"le", "les", "des", "une", "est", "dans", "pour", "qui", "sur", "avec", "aux", "été", "après", "du"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "mit", "für", "auf", "dem", "den", "ein", "eine", "von", "nach"},
    "it": {"il", "gli", "della", "dei", "che", "per", "con", "non", "una", "sono", "nel", "alla", "dopo", "è"},
    "tr": {"ve", "bir", "bu", "için", "ile", "olarak", "daha", "çok", "oldu", "göre", "sonra", "ise"},
    "vi": {"và", "của", "các", "là", "trong", "được", "cho", "với", "một", "những", "đã", "người", "này"},
    "nl": {"het", "een", "van", "voor", "niet", "zijn", "met", "naar", "ook", "werd", "heeft", "dat"},
    "pl": {"się", "nie", "jest", "na", "do", "że", "przez", "oraz", "jak", "dla", "został", "po"},
    "en": {"the", "and", "of", "to", "in", "is", "was", "for", "on", "that", "with", "as", "by", "at", "has", "said", "from"},
}


def language_of(text: str) -> str:
    """ISO code guess for an article: script first, then stop words for Latin-script languages."""
    text = re.sub(r"\(?https?://\S+|\bwww\.\S+", " ", text or "")[:800]  # links would count as words ("com" is Portuguese)
    if re.search(r"[぀-ヿ]", text):
        return "ja"
    if re.search(r"[가-힯]", text):
        return "ko"
    if len(re.findall(r"[一-鿿]", text)) >= 4:
        return "zh"
    for code, pattern in (("th", r"[฀-๿]"), ("ar", r"[؀-ۿ]"), ("hi", r"[ऀ-ॿ]"), ("ru", r"[Ѐ-ӿ]")):
        if len(re.findall(pattern, text)) >= 6:
            return code
    words = re.findall(r"[^\W\d_]+", text.lower())
    if not words:
        return "en"
    best, hits = "en", 0
    for code, stop in _STOP.items():
        n = sum(w in stop for w in words)
        if n > hits:
            best, hits = code, n
    return best


# --- a free router: where does the clue's story happen? --------------------------------------------------
_ZH_PLACES = (
    r"\b(china|chinese|mainland|beijing|shanghai|hong kong|macau|macao|taiwan|taiwanese|taipei|singapore|singaporean|malaysia|malaysian|kuala lumpur|"
    r"guangdong|shenzhen|guangzhou|yangtze|yuan|renminbi|city-state|kaohsiung|taichung|tainan|penang|johor|sabah|sarawak|ringgit|hang seng|"
    r"sichuan|zhejiang|jiangsu|hainan|xinjiang|tibet|hubei|wuhan|chongqing|tianjin|fujian|shandong|henan|yunnan|cross-strait|kuomintang|legislative yuan|politburo)\b"
)
_ID_PLACES = (
    r"\b(indonesia|indonesian|jakarta|java|javanese|sumatra|sumatran|bali|balinese|sulawesi|kalimantan|borneo|papua|papuan|rupiah|"
    r"surabaya|bandung|medan|aceh|regency|regencies|lombok|maluku|riau|banten|yogyakarta|semarang|makassar|nusantara|sub-district|subdistrict)\b"
)
# Places whose local press writes in another language the News Search API can filter on. General knowledge, not
# fitted to the lab's clues; words that are also ordinary English ("won", "real", "dong", "jordan") are left out.
_OTHER_PLACES = {
    "ja": r"\b(japan|japanese|tokyo|osaka|prefecture|prefectural|yen|hokkaido|kyushu|shikoku|honshu|okinawa|kyoto)\b",
    "ko": r"\b(korea|korean|seoul|busan)\b",
    "th": r"\b(thailand|thai|bangkok|baht)\b",
    "vi": r"\b(vietnam|vietnamese|hanoi|ho chi minh|mekong)\b",
    "hi": r"\b(india|indian|delhi|mumbai|rupee|rupees|crore|lakh|uttar pradesh|bihar|south asian)\b",
    "ar": r"\b(saudi|egypt|egyptian|cairo|nile|riyadh|gulf|emirat\w*|qatar\w*|kuwait\w*|iraq|iraqi|syria|syrian|lebanon|lebanese|jordanian|morocc\w*|tunisia\w*|algeria\w*|arab|arabic|governorate|maghreb|levant|north african|middle eastern|middle east|yemen\w*|libya\w*|oman\w*|bahrain\w*|sudan\w*)\b",
    "es": r"\b(spain|spanish|mexic\w*|argentin\w*|colombia\w*|chile|chilean|peru|peruvian|venezuela\w*|south american|latin american|central american|peso|pesos|madrid|andean|ecuador\w*|bolivia\w*|uruguay\w*|paraguay\w*|cuba|cuban|iberian|catalan|catalonia)\b",
    "pt": r"\b(brazil\w*|portug\w*|lisbon|sao paulo|são paulo|reais|rio de janeiro|angola\w*|mozambi\w*)\b",
    "fr": r"\b(france|french|paris|belgi\w*|quebec|senegal\w*|ivory coast|west african|sahel|western european|francophone)\b",
    "de": r"\b(german\w*|berlin|austria\w*|vienna|bavaria\w*|swiss|switzerland|bundestag|bundesliga)\b",
    "it": r"\b(ital\w*|rome|milan|sicil\w*)\b",
    "tr": r"\b(turk\w*|ankara|istanbul|lira)\b",
    "ru": r"\b(russia\w*|moscow|kremlin|rouble|ruble|central asian|kazakh\w*|belarus\w*)\b",
    "nl": r"\b(dutch|netherlands|amsterdam|flemish)\b",
    "pl": r"\b(poland|polish|warsaw|zloty)\b",
}


def route(clue: str) -> list[str]:
    """Languages the clue's place words point to, most specific first. [] when the clue names no place."""
    low = clue.lower()
    hits = []
    if re.search(_ID_PLACES, low):
        hits.append("id")
    if re.search(_ZH_PLACES, low):
        hits.append("zh")
    for code, pattern in _OTHER_PLACES.items():
        if re.search(pattern, low):
            hits.append(code)
    return hits
