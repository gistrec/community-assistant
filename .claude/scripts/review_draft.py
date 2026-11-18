#!/usr/bin/env python3
"""Pre-publish review for community-assistant draft replies.

Usage::

    python3 .claude/scripts/review_draft.py DRAFT.json \\
        [--candidates DIR/candidates.json] [--out REVIEW.json]

DRAFT.json is the draft file also consumed by ``publish_issue.py``::

    {"url": "<discussion url>", "topic": "...", "draft": "...", ...}

With ``--candidates`` the discussion (title, author, body, comments, replies)
is looked up by ``url`` in the file written by ``discover.py``, so it never
has to be copied by hand. Without it the payload must carry the discussion
inline::

    {"discussion": {"title": "...", "url": "...", "topic": "...",
                    "author": "...", "body": "...",
                    "comments": [{"author": "...", "body": "...",
                                  "replies": [{"author": "...", "body": "..."}]}]},
     "draft": "..."}

Calls the OpenAI Chat Completions endpoint to evaluate the draft against the
project's posting rules. Requires ``OPENAI_API_KEY`` in the environment.
Model defaults to ``gpt-5.5`` at ``reasoning_effort: high``, falling back to
``gpt-5.4`` when the account lacks access to the flagship; override with
``OPENAI_REVIEW_MODEL`` / ``OPENAI_REVIEW_EFFORT``.

Prints a single-line JSON result to stdout (and to ``--out``). On success::

    {"ok": true, "verdict": "approve|revise|reject", "issues": [...],
     "rationale": "...", "model": "<model id used>", "draft_sha256": "..."}

On failure (network, missing key, malformed model output)::

    {"ok": false, "error": "..."}

A draft containing a bare ``@name`` is refused before any API call
(``"lint": [...]``, exit code 2): fix the draft and run again - that does
not count as a review round.

Exit code is ``0`` on a completed review (any verdict), ``2`` on a lint
refusal and ``1`` on failure. Transient failures (HTTP 429 / 5xx, timeouts)
are retried once inside the script; the caller never retries.

The review is advisory: ``publish_issue.py`` publishes the draft whatever
the verdict (and after a failed call) and records the outcome in the issue.
Always pass ``--out`` so that record exists.
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

import ca_common as ca


DEFAULT_MODELS = ("gpt-5.5", "gpt-5.4")
TIMEOUT_SECONDS = 300
RETRY_DELAY_SECONDS = 10

# Used only if the "Target topics" section of CLAUDE.md cannot be parsed.
FALLBACK_TOPICS = [
    "C++ (modern C++, std library, build / link issues)",
    "Python (packaging, environments, common patterns)",
    "CMake",
    "vcpkg / Conan",
    "header-only libraries",
    "GitHub Actions / CI",
    "packaging",
    "geospatial algorithms",
    "MCP (Model Context Protocol) servers / SDKs, FastMCP",
    "Python web backend: FastAPI, pydantic, SQLAlchemy, Alembic, httpx",
    "Infra / DevOps: Docker Compose, nginx, Cloudflare, S3 / boto3",
]


SYSTEM_PROMPT_TEMPLATE = """You are a strict reviewer for an automated GitHub Discussions reply bot. The bot drafts short replies to fresh, unanswered questions in third-party repos. A human maintainer reviews every draft before posting it manually. Your job is to catch problems BEFORE drafts reach the maintainer's queue, so weak ones don't waste review time.

Apply the project's posting rules:
1. Draft must be short, practical, and technically correct. Aim for 3-10 lines (code blocks included); up to about 15 lines is acceptable when the content needs it. Only flag length beyond that, or padding.
2. Must NOT invent APIs, flags, compiler options, or library behaviors. Citing well-known patterns is fine; making up function names, signatures, or flags is not.
3. The draft must be the FIRST substantive answer in the thread. If any existing comment or reply from someone other than the discussion author (OP) already attempts a direct answer (a solution, explanation, workaround, or diagnosis) - even a partial, unconfirmed, or wrong one - REJECT. Correcting or extending existing answers is out of scope for this bot. Comments like "+1", bot notices, requests for more info, and OP's own follow-ups do not count as answer attempts. Exception: OP answering their own question ("figured it out: ...") means the thread is solved - REJECT.
4. Must NOT mainly promote a specific tool, library, blog post, or someone's profile.
5. Must NOT contain bare @<login> mentions anywhere. Only the markdown-link form is allowed: [@login](https://github.com/login). A bare "@login" in the draft is a HIGH-severity issue. Text inside code spans or code blocks (decorators, npm scopes) is not a mention.
6. Must actually answer the question being asked, not a related one.
7. Must not be in a skip-domain (legal, security-sensitive, medical, financial, personal).
8. Topic must map to one of the target topics:
__TOPICS__

Return STRICT JSON only - no prose, no markdown, no code fences:

{
  "verdict": "approve" | "revise" | "reject",
  "issues": [
    {"severity": "low" | "medium" | "high", "category": "<short tag like 'not_first_answer', 'duplication', 'invented_api', 'tone', 'promotion', 'bare_mention', 'off_topic', 'length', 'skip_domain'>", "description": "<concrete one-liner>"}
  ],
  "rationale": "<1-2 sentence overall judgment>"
}

Verdict rules:
- approve: post as-is; no issues or only nitpicks.
- revise: fixable problems (wording, tone, minor inaccuracy, missing caveat); core idea sound. The bot applies your issues to the draft and resubmits it for one re-review, so make each issue concrete and actionable.
- reject: wrong, not the first answer in the thread, duplicative, promotional, off-topic, in a skip-domain, contains bare @-mentions, or otherwise should not be posted at all.

If any existing comment already attempts to answer the question, default to reject.
"""


def system_prompt():
    topics = ca.claude_md_bullets("Target topics") or FALLBACK_TOPICS
    return SYSTEM_PROMPT_TEMPLATE.replace(
        "__TOPICS__", "\n".join("   - " + t for t in topics))


def _truncate(text, limit):
    if text is None:
        return ""
    s = str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + "...[truncated]"


def _who(author, op):
    author = str(author or "(unknown)")
    return author + " (OP)" if op and author == op else author


def build_user_message(disc, topic, draft):
    title = _truncate(disc.get("title", ""), 500)
    url = disc.get("url", "") or ""
    op = disc.get("author") or ""
    body = _truncate(disc.get("body", ""), 6000)

    comments = disc.get("comments") or []
    lines = []
    for c in comments[:10]:
        c = c or {}
        lines.append("- [" + _who(c.get("author"), op) + "] "
                     + _truncate(c.get("body", ""), 1500))
        for r in (c.get("replies") or [])[:10]:
            r = r or {}
            lines.append("  - reply [" + _who(r.get("author"), op) + "] "
                         + _truncate(r.get("body", ""), 1000))
    comments_block = "\n".join(lines) if lines else "(none)"
    total = disc.get("comment_total", len(comments))

    return (
        "Discussion title: " + title + "\n"
        "Discussion URL: " + url + "\n"
        "Discussion author (OP): " + (op or "(unknown)") + "\n"
        "Topic mapping: " + (topic or "") + "\n\n"
        "Discussion body:\n" + body + "\n\n"
        "Existing comments and replies (count: " + str(total) + "):\n"
        + comments_block + "\n\n"
        "Draft reply to review:\n" + _truncate(draft, 4000)
    )


def call_openai(api_key, model, effort, system_msg, user_msg):
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    }
    if effort and model.startswith(("gpt-5", "o")):
        payload["reasoning_effort"] = effort
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def call_with_retry(api_key, model, effort, system_msg, user_msg):
    """One retry on transient failures (429, 5xx, timeout / connection drop)."""
    try:
        return call_openai(api_key, model, effort, system_msg, user_msg)
    except urllib.error.HTTPError as e:
        if e.code != 429 and e.code < 500:
            raise
        why = "HTTP " + str(e.code)
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        why = repr(e)
    sys.stderr.write("review call failed (" + why + "), retrying once in "
                     + str(RETRY_DELAY_SECONDS) + "s\n")
    time.sleep(RETRY_DELAY_SECONDS)
    return call_openai(api_key, model, effort, system_msg, user_msg)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", help="draft JSON file")
    ap.add_argument("--candidates", help="candidates.json written by discover.py")
    ap.add_argument("--out", help="also write the result JSON to this file")
    try:
        args = ap.parse_args()
    except SystemExit:
        ca.emit({"ok": False, "error": "Usage: review_draft.py <draft-json> "
                 "[--candidates FILE] [--out FILE]"})
        return 1

    def finish(obj, code):
        ca.emit(obj)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
        return code

    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return finish({"ok": False, "error": "OPENAI_API_KEY is not set"}, 1)

    override = (os.environ.get("OPENAI_REVIEW_MODEL") or "").strip()
    candidates = [override] if override else list(DEFAULT_MODELS)
    effort = (os.environ.get("OPENAI_REVIEW_EFFORT") or "high").strip()

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            raise ValueError("top-level JSON value must be an object")
        disc = payload.get("discussion")
        if args.candidates:
            disc = ca.find_candidate(ca.load_candidates(args.candidates),
                                     payload.get("url") or (disc or {}).get("url"))
            if not disc:
                raise ValueError("url not found in candidates file")
        if not isinstance(disc, dict):
            raise ValueError("no discussion: pass --candidates or an inline 'discussion'")
    except Exception as e:
        return finish({"ok": False, "error": "Could not read input: " + repr(e)}, 1)

    draft = payload.get("draft") or ""
    topic = payload.get("topic") or disc.get("topic") or ""
    if not draft.strip():
        return finish({"ok": False, "error": "draft is empty"}, 1)

    mentions = ca.find_bare_mentions(draft)
    if mentions:
        return finish({
            "ok": False, "lint": mentions,
            "error": "draft contains bare @mentions: " + ", ".join(mentions)
                     + " - put code in backticks or use the link form "
                       "[@login](https://github.com/login), then run again",
        }, 2)

    system_msg = system_prompt()
    user_msg = build_user_message(disc, topic, draft)

    response = None
    model_used = None
    for i, model in enumerate(candidates):
        try:
            response = call_with_retry(api_key, model, effort, system_msg, user_msg)
            model_used = model
            break
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            unavailable = e.code in (403, 404) or "model_not_found" in body
            if unavailable and i + 1 < len(candidates):
                sys.stderr.write("review model " + model + " unavailable, "
                                 "trying " + candidates[i + 1] + "\n")
                continue
            return finish({"ok": False, "error": "HTTP " + str(e.code)
                           + " (model " + model + "): " + body[:500]}, 1)
        except Exception as e:
            return finish({"ok": False, "error": "Network/runtime: " + repr(e)}, 1)

    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return finish({"ok": False,
                       "error": "Unexpected API response shape: " + repr(e)}, 1)

    if not isinstance(content, str) or not content.strip():
        # e.g. a refusal: content is null and the text sits in message.refusal
        return finish({"ok": False, "error": "Model returned no content"}, 1)

    try:
        review = json.loads(content)
    except ValueError as e:
        return finish({"ok": False, "error": "Model did not return JSON: " + str(e),
                       "raw": content[:500]}, 1)
    if not isinstance(review, dict):
        return finish({"ok": False, "error": "Model returned non-object JSON",
                       "raw": content[:500]}, 1)

    verdict = review.get("verdict")
    if verdict not in ("approve", "revise", "reject"):
        return finish({"ok": False, "error": "Unknown verdict: " + repr(verdict),
                       "raw": content[:500]}, 1)

    raw_issues = review.get("issues") or []
    if not isinstance(raw_issues, list):
        raw_issues = []

    issues = []
    for it in raw_issues:
        if not isinstance(it, dict):
            continue
        severity = str(it.get("severity", "low")).lower()
        if severity not in ("low", "medium", "high"):
            severity = "low"
        issues.append({
            "severity": severity,
            "category": str(it.get("category", "general")),
            "description": str(it.get("description", "")),
        })

    return finish({
        "ok": True,
        "verdict": verdict,
        "issues": issues,
        "rationale": str(review.get("rationale", "")),
        "model": str(response.get("model") or model_used),
        "draft_sha256": ca.sha256_text(draft),
        "url": disc.get("url"),
    }, 0)


if __name__ == "__main__":
    sys.exit(main())
