# lanes: how the two search calls are spent

Working notes. All numbers on dev (150 clues, 120 real) unless a sample size is given. "Key reached" = a key
article (in the required language for local clues) is in the results of either call; same measure as bench's
"key article in results". Tools: `lab/strategies/lanes_probe.py` (run single lanes, cached), `lanes_sim.py`
(offline: which pair of lanes reaches the key), `lanes_common.py` (lane bodies, language guess, place rule).

## What each lane returns
- any-language lane: 1466 of 1500 results are English (98%). 0/24 local_zh, 0/29 local_id, 0/9 other_language keys.
- language=["en"] vs no filter (41 clues: 12 random all-English, 29 with a non-English result): identical list
  for 28, 7 to 10 of 10 urls shared for the rest, no key gained or lost. "English only" = the baseline's first call.
- zh+id lane: 937 zh, 521 id of 1500.
- [zh] alone vs [zh,id] (12 local_zh clues, incl. the 3 baseline misses): same 9/12. [id] alone vs [zh,id]
  (10 local_id, incl. the miss): same 9/10. Ranks within 1. Splitting buys nothing; the misses are query misses.
- wide lane (all 17 API languages except English): result language follows the story's country.
  other_language 9/9 (rank 1 to 3), plain pt/fr 3/3. But local_id 25/29 (zh+id: 28), local_zh 20/24 (21).
- wide with count=50 on the 7 clues where wide(10) lost a key zh+id had: recovered 2 of 7, at ranks 34 and 48.
  A deep wide call cannot stand in for the zh+id call.

## Key reached, by pair of calls (dev, 120 real; fake column = real source article reached, n=30)
| allocation | fake | local_id 29 | local_zh 24 | minimal 5 | other 9 | plain 47 | twin 6 | real |
|---|---|---|---|---|---|---|---|---|
| baseline any + zh+id | 21 | 28 | 21 | 3 | 0 | 39 | 6 | 97 (80.8%) |
| any + wide | 18 | 25 | 20 | 2 | 9 | 40 | 6 | 102 (85.0%) |
| zh+id + wide (no any) | 16 | 28 | 21 | 1 | 9 | 14 | 3 | 76 (63.3%) |
| rule v1: other place -> wide, else zh+id | 21 | 28 | 21 | 3 | 4 | 42 | 6 | 104 (86.7%) |
| rule v2 (wider word list, see caveat) | 21 | 28 | 21 | 3 | 6 | 42 | 6 | 106 (88.3%) |
| rule: zh/id place -> zh+id, else wide | 20 | 25 | 21 | 2 | 9 | 41 | 6 | 104 (86.7%) |
| sequential, wide first, then zh+id if >=6 zh/id results else any | 18 | 25 | 20 | 1 | 9 | 38 | 6 | 99 (82.5%) |
| oracle (best second lane per clue) | 21 | 28 | 21 | 3 | 9 | 42 | 6 | 109 (90.8%) |

Caveat on rule v2: "nile", "governorate", "western european" and a few region words were added after reading the
dev clues; 2 of its 6 other_language hits (c092, c095) depend on them. v1 (written before looking) is the fair number.
