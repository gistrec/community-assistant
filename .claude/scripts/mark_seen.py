#!/usr/bin/env python3
"""Record triage decisions so later runs don't re-read the same threads.

Usage::

    python3 .claude/scripts/mark_seen.py --candidates DIR/candidates.json DECISIONS.json

DECISIONS.json maps a reason to candidate numbers (``n`` from index.md) or
discussion URLs::

    {"answered": [3, 7], "off_topic": [1, 2, 5, 9], "needs_info": [12]}

Permanent reasons (never shown again): answered, off_topic, not_a_question,
skip_domain, promo, review_rejected, published.
Recheck reasons (shown again once the thread changes): needs_info,
low_confidence.

Do not mark drafts dropped because the review *call* failed - they should
be retried by the next run. Local file write only; no network.
"""

import argparse
import json
import sys

import ca_common as ca


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("decisions")
    ap.add_argument("--candidates", required=True)
    args = ap.parse_args()

    try:
        candidates = ca.load_candidates(args.candidates)
        with open(args.decisions, "r", encoding="utf-8") as f:
            decisions = json.load(f)
    except Exception as e:
        ca.emit({"ok": False, "error": "could not read input: " + repr(e)})
        return 1
    if not isinstance(decisions, dict):
        ca.emit({"ok": False, "error": "decisions must be an object: reason -> list"})
        return 1

    by_n = {c.get("n"): c for c in candidates}
    cache = ca.load_cache()
    marked, problems = {}, []

    for reason, items in decisions.items():
        if reason not in ca.REASONS:
            problems.append("unknown reason " + repr(reason)
                            + " (valid: " + ", ".join(ca.REASONS) + ")")
            continue
        for item in items if isinstance(items, list) else [items]:
            cand = (by_n.get(item) if isinstance(item, int)
                    else ca.find_candidate(candidates, str(item)))
            if not cand:
                problems.append("no such candidate: " + repr(item))
                continue
            ca.cache_mark(cache, cand, reason)
            marked[reason] = marked.get(reason, 0) + 1

    ca.save_cache(cache)
    ca.emit({"ok": not problems, "marked": marked, "problems": problems[:20],
             "cache": str(ca.cache_path()), "entries": len(cache)})
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
