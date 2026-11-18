#!/usr/bin/env python3
"""Turn a reviewed draft into an issue in gistrec/community-assistant.

Usage::

    python3 .claude/scripts/publish_issue.py DRAFT.json \\
        --candidates DIR/candidates.json \\
        --review REVIEW1.json [--review REVIEW2.json] \\
        [--ack-comments N] [--dry-run]

DRAFT.json::

    {
      "url": "<discussion url>",
      "topic": "<one of the target topics>",
      "confidence": "high" | "medium",
      "why_useful": "<short reason>",
      "context": "<1-3 sentences>",
      "draft": "<final reply text>",
      "original_draft": "<round 1 text; required when round 1 said revise>"
    }

REVIEW files are the ``--out`` files of ``review_draft.py``, in round order.

The review is **advisory**: every verdict is published, including ``reject``
and a failed review call - the issue records what the reviewer said and the
maintainer decides. A rejected draft additionally gets the
``review-rejected`` label.

What the script enforces before the single write call (``gh issue create``
on this repo - the only write anywhere in these scripts):

- the review bookkeeping: one review file when round 1 said ``approve`` /
  ``reject`` or the call failed; two when round 1 said ``revise``. A
  completed review must be of exactly the text it is recorded against
  (sha256 check), so the issue never misreports what was reviewed;
- no bare ``@name`` in the draft; every other part of the body (context,
  reviewer remarks, original draft) is neutralised automatically;
- the discussion URL is not already tracked in this repo's issues;
- the thread is still open, unanswered and has no new comments since
  discovery. New comments exit with code 3 and are printed: read them, and
  only if none is an answer attempt re-run with ``--ack-comments <total>``.

Prints a single-line JSON result. Exit codes: 0 created (or dry run),
3 thread changed, 1 anything else (nothing was created).
"""

import argparse
import json
import sys
import tempfile

import ca_common as ca


BASE_LABELS = "draft,github-discussion,needs-review"
REJECTED_LABEL = "review-rejected"
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def link_text(text):
    return " ".join((text or "").split()).replace("[", "\\[").replace("]", "\\]")


def flat(text):
    return " ".join(str(text or "").split())


def review_state(review):
    """approve | revise | reject | failed (call did not complete) | lint."""
    if not isinstance(review, dict):
        return "failed"
    if review.get("lint"):
        return "lint"
    if not review.get("ok") or review.get("verdict") not in ("approve", "revise", "reject"):
        return "failed"
    return review["verdict"]


def check_reviews(draft, reviews):
    """Returns ``(error, states)``. The verdicts themselves never block."""
    states = [review_state(r) for r in reviews]
    if "lint" in states:
        return ("a review file is a lint refusal, not a review: fix the bare "
                "@mention in the draft and run review_draft.py again"), states
    for i, (r, state) in enumerate(zip(reviews, states), 1):
        if state != "failed" and r.get("url") and r["url"].lower() != draft["url"].lower():
            return "review %d is for a different discussion" % i, states

    final_sha = ca.sha256_text(draft["draft"])
    r1, s1 = reviews[0], states[0]
    if s1 != "revise":
        if len(reviews) != 1:
            return "round 1 was '%s': pass exactly one --review" % s1, states
        if s1 != "failed" and r1.get("draft_sha256") != final_sha:
            return "the draft text differs from what round 1 reviewed", states
        return None, states

    if len(reviews) != 2:
        return "round 1 said revise: pass the round 2 review file as well", states
    original = draft.get("original_draft") or ""
    if not original.strip():
        return "round 1 said revise: 'original_draft' is required", states
    if r1.get("draft_sha256") != ca.sha256_text(original):
        return "'original_draft' differs from what round 1 reviewed", states
    if states[1] != "failed" and reviews[1].get("draft_sha256") != final_sha:
        return "the draft text differs from what round 2 reviewed", states
    return None, states


def round_lines(number, review, state, mark):
    if state == "failed":
        error = flat((review or {}).get("error") if isinstance(review, dict) else "")
        return ["- **Round %d:** unavailable — %s" % (number, error[:300] or "review call failed")]
    lines = ["- **Round %d:** %s — %s" % (number, state, flat(review.get("rationale")))]
    issues = sorted(review.get("issues") or [],
                    key=lambda it: SEVERITY_ORDER.get(it.get("severity"), 3))
    lines += ["  - **(%s)** [%s] %s — %s" % (
        it.get("severity", "low"), it.get("category", "general"),
        flat(it.get("description")), mark) for it in issues]
    return lines


def review_section(draft, reviews, states):
    """Everything but a clean round-1 approve gets a section."""
    lines = ["## ChatGPT review", ""]
    two_rounds = len(reviews) == 2
    # Round 1 issues were applied only if a revision round followed.
    lines += round_lines(1, reviews[0], states[0], "applied" if two_rounds else "outstanding")
    if two_rounds:
        lines += round_lines(2, reviews[1], states[1], "outstanding")

    models = [r.get("model") for r in reviews if isinstance(r, dict) and r.get("model")]
    if models:
        lines += ["", "Review model: " + str(models[-1])]

    final = states[-1]
    closing = {
        "reject": "**The reviewer recommends not posting this draft.** The review "
                  "is advisory — check its reasons against the thread and decide.",
        "revise": "Resolve the outstanding issues before posting.",
        "failed": ("The revised draft was not re-reviewed." if two_rounds else
                   "This draft was not reviewed — check it more carefully."),
    }.get(final)
    if closing:
        lines += ["", closing]

    text = ca.neutralize_mentions("\n".join(lines))
    if two_rounds:
        original = ca.neutralize_mentions(draft["original_draft"].strip())
        text += ("\n\n<details>\n<summary>Original draft (before review fixes)"
                 "</summary>\n\n" + original + "\n\n</details>")
    return text


def build_body(draft, cand, reviews, states):
    repo, author = cand["repo"], cand["author"]
    author_md = (author if author == "(ghost)"
                 else "[%s](https://github.com/%s)" % (author, author))
    parts = [
        "## Discussion",
        "",
        "- **Discussion:** [%s](%s)" % (link_text(cand["title"]), cand["url"]),
        "- **Repository:** [%s](https://github.com/%s)" % (repo, repo),
        "- **Author:** " + author_md,
        "- **Updated:** " + str(cand.get("updated_at") or ""),
        "- **Topic:** " + ca.neutralize_mentions(flat(draft["topic"])),
        "- **Confidence:** " + draft["confidence"],
        "- **Why useful:** " + ca.neutralize_mentions(flat(draft["why_useful"])),
        "",
        "## Context",
        "",
        ca.neutralize_mentions(draft["context"].strip()),
        "",
        "## Draft reply",
        "",
        draft["draft"].strip(),
        "",
    ]
    if states != ["approve"]:
        parts += [review_section(draft, reviews, states), ""]
    parts += [
        "## Checklist",
        "",
        "- [ ] I reviewed the original discussion",
        "- [ ] I verified the thread still has no direct answer (nothing new since drafting)",
        "- [ ] I posted the reply manually",
    ]
    return "\n".join(parts) + "\n"


def new_comments(fresh, cand):
    known = {(c["author"], c["created_at"]) for c in cand.get("comments") or []}
    known |= {(r["author"], r["created_at"])
              for c in cand.get("comments") or [] for r in c.get("replies") or []}
    out = []
    for c in fresh.get("comments") or []:
        for entry in [c] + (c.get("replies") or []):
            if (entry["author"], entry["created_at"]) not in known:
                out.append({"author": entry["author"], "body": entry["body"][:1500]})
    return out


def create_issue(title, body, label_sets):
    """Try each label set in turn; labels missing in the repo are never
    auto-created, the issue just gets fewer (or no) labels."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8",
                                     delete=True) as f:
        f.write(body)
        f.flush()
        base = ["issue", "create", "-R", ca.REPO, "--title", title,
                "--body-file", f.name]
        code, out, err = 1, "", "no attempt made"
        for labels in label_sets:
            code, out, err = ca.run_gh(base + (["--label", labels] if labels else []),
                                       timeout=120)
            if code == 0:
                return out, labels, None
            if "label" not in err.lower():
                break
    return None, None, err.strip()[:300]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("draft")
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--review", action="append", default=[], required=True)
    ap.add_argument("--ack-comments", type=int, default=None,
                    help="comment total (incl. replies) you have read and judged "
                         "to contain no answer attempt")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every check and print the body; create nothing")
    args = ap.parse_args()

    def fail(msg, code=1, **extra):
        out = {"ok": False, "error": msg}
        out.update(extra)
        ca.emit(out)
        return code

    try:
        draft = load_json(args.draft)
        candidates = ca.load_candidates(args.candidates)
    except Exception as e:
        return fail("could not read input: " + repr(e))
    reviews = []
    for path in args.review:
        try:
            reviews.append(load_json(path))
        except Exception as e:   # an unreadable review counts as a failed call
            reviews.append({"ok": False, "error": "review file unreadable: " + repr(e)})

    for field in ("url", "topic", "confidence", "why_useful", "context", "draft"):
        if not isinstance(draft.get(field), str) or not draft[field].strip():
            return fail("draft file: missing field '" + field + "'")
    if draft["confidence"] not in ("high", "medium"):
        return fail("confidence must be high or medium (low drafts are skipped)")

    cand = ca.find_candidate(candidates, draft["url"])
    if not cand:
        return fail("url not found in candidates file")

    error, states = check_reviews(draft, reviews)
    if error:
        return fail("review files: " + error)

    mentions = ca.find_bare_mentions(draft["draft"])
    if mentions:
        return fail("draft contains bare @mentions: " + ", ".join(mentions))

    cache = ca.load_cache()
    try:
        if cand["url"].lower() in ca.tracked_urls():
            ca.cache_mark(cache, cand, "published")
            ca.save_cache(cache)
            return fail("already reported: an issue for this discussion exists")
        fresh = ca.fetch_discussion(cand["url"])
    except ca.GhError as e:
        return fail("GitHub check failed: " + str(e))

    if fresh["answered"] or fresh["closed"] or fresh["locked"]:
        ca.cache_mark(cache, fresh, "answered")
        ca.save_cache(cache)
        return fail("thread is now answered, closed or locked - dropped")
    if (fresh["comment_total"] != cand.get("comment_total", 0)
            and fresh["comment_total"] != args.ack_comments):
        return fail(
            "thread changed since discovery (%d -> %d comments). Read the new "
            "comments below: if any is an answer attempt drop the draft, "
            "otherwise re-run with --ack-comments %d"
            % (cand.get("comment_total", 0), fresh["comment_total"],
               fresh["comment_total"]),
            code=3, new_comments=new_comments(fresh, cand))

    cand = dict(cand, updated_at=fresh["updated_at"])
    title = "Draft: %s — %s" % (cand["repo"], flat(cand["title"]))
    body = build_body(draft, cand, reviews, states)

    final = states[-1]
    label_sets = [BASE_LABELS, ""]
    if final == "reject":
        label_sets.insert(0, BASE_LABELS + "," + REJECTED_LABEL)

    if args.dry_run:
        ca.emit({"ok": True, "dry_run": True, "title": title,
                 "labels": label_sets[0], "body": body})
        return 0

    try:
        out, labels, err = create_issue(title, body, label_sets)
    except ca.GhError as e:
        return fail("gh issue create failed: " + str(e))
    if out is None:
        return fail("gh issue create failed: " + err)

    ca.cache_mark(cache, fresh, "published")
    ca.save_cache(cache)
    models = [r.get("model") for r in reviews if isinstance(r, dict) and r.get("model")]
    ca.emit({"ok": True, "issue": out.strip().splitlines()[-1] if out.strip() else "",
             "title": title, "labels": labels, "rounds": len(reviews),
             "final_verdict": final, "model": models[-1] if models else None})
    return 0


if __name__ == "__main__":
    sys.exit(main())
