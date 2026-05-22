#!/usr/bin/env python3
"""Pre-publish review for community-assistant draft replies.

Reads a JSON payload at ``argv[1]`` with this shape::

    {
      "discussion": {
        "title": "...",
        "url": "...",
        "topic": "...",
        "body": "...",
        "answer": "..." | null,
        "comments": [{"author": "...", "body": "..."}, ...]
      },
      "draft": "..."
    }

Calls the OpenAI Chat Completions endpoint to evaluate the draft against the
project's posting rules. Requires ``OPENAI_API_KEY`` in the environment.
Model defaults to ``gpt-5``; override with ``OPENAI_REVIEW_MODEL`` if needed.

Prints a single-line JSON result to stdout. On success::

    {"ok": true, "verdict": "approve|revise|reject",
     "issues": [...], "rationale": "..."}

On failure (network, missing key, malformed model output)::

    {"ok": false, "error": "..."}

Exit code is ``0`` on a completed review (any verdict) and ``1`` on failure.
The caller (``daily.md``) treats non-zero exit codes as fail-closed and drops
the draft.
"""

import json
import os
import sys
import urllib.error
import urllib.request


SYSTEM_PROMPT = """You are a strict reviewer for an automated GitHub Discussions reply bot. The bot drafts short replies to fresh, unanswered questions in third-party repos. A human maintainer reviews every draft before posting it manually. Your job is to catch problems BEFORE drafts reach the maintainer's queue, so weak ones don't waste review time.

Apply the project's posting rules:
1. Draft must be 3-10 lines, practical, and technically correct.
2. Must NOT invent APIs, flags, compiler options, or library behaviors. Citing well-known patterns is fine; making up function names, signatures, or flags is not.
3. Must NOT duplicate the chosen answer or any existing comment. If existing comments already cover the same ground, REJECT unless the draft adds a clearly useful correction or missing detail.
4. Must NOT mainly promote a specific tool, library, blog post, or someone's profile.
5. Must NOT contain bare @<login> mentions anywhere. Only the markdown-link form is allowed: [@login](https://github.com/login). A bare "@login" in the draft is a HIGH-severity issue.
6. Must actually answer the question being asked, not a related one.
7. Must not be in a skip-domain (legal, security-sensitive, medical, financial, personal).
8. Topic must map to: C++, Python, CMake, vcpkg, Conan, header-only libraries, GitHub Actions, packaging, or geospatial algorithms.

Return STRICT JSON only - no prose, no markdown, no code fences:

{
  "verdict": "approve" | "revise" | "reject",
  "issues": [
    {"severity": "low" | "medium" | "high", "category": "<short tag like 'duplication', 'invented_api', 'tone', 'promotion', 'bare_mention', 'off_topic', 'length', 'skip_domain'>", "description": "<concrete one-liner>"}
  ],
  "rationale": "<1-2 sentence overall judgment>"
}

Verdict rules:
- approve: post as-is; no issues or only nitpicks.
- revise: fixable problems (wording, tone, minor inaccuracy, missing caveat); core idea sound; maintainer should edit before posting.
- reject: wrong, duplicative, promotional, off-topic, in a skip-domain, contains bare @-mentions, or otherwise should not be posted at all.

If existing comments already adequately answer the question and the draft adds nothing novel, default to reject.
"""


def _truncate(text, limit):
    if text is None:
        return ""
    s = str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + "...[truncated]"


def build_user_message(payload):
    disc = payload.get("discussion") or {}
    draft = payload.get("draft") or ""

    title = _truncate(disc.get("title", ""), 500)
    url = disc.get("url", "") or ""
    topic = disc.get("topic", "") or ""
    body = _truncate(disc.get("body", ""), 4000)
    answer = disc.get("answer")
    answer_text = _truncate(answer, 2000) if answer else "(none)"

    comments = disc.get("comments") or []
    if not comments:
        comments_block = "(none)"
    else:
        lines = []
        for c in comments[:10]:
            author = (c or {}).get("author") or "(unknown)"
            cbody = _truncate((c or {}).get("body", ""), 1500)
            lines.append("- [" + str(author) + "] " + cbody)
        comments_block = "\n".join(lines)

    return (
        "Discussion title: " + title + "\n"
        "Discussion URL: " + url + "\n"
        "Topic mapping: " + topic + "\n\n"
        "Discussion body:\n" + body + "\n\n"
        "Chosen answer:\n" + answer_text + "\n\n"
        "Existing comments (count: " + str(len(comments)) + "):\n" + comments_block + "\n\n"
        "Draft reply to review:\n" + _truncate(draft, 4000)
    )


def call_openai(api_key, model, system_msg, user_msg):
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    if len(sys.argv) != 2:
        _emit({"ok": False, "error": "Usage: review_draft.py <input-json-path>"})
        return 1

    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        _emit({"ok": False, "error": "OPENAI_API_KEY is not set"})
        return 1

    model = (os.environ.get("OPENAI_REVIEW_MODEL") or "gpt-5").strip()

    try:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        _emit({"ok": False, "error": "Could not read input: " + repr(e)})
        return 1

    user_msg = build_user_message(payload)

    try:
        response = call_openai(api_key, model, SYSTEM_PROMPT, user_msg)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        _emit({"ok": False, "error": "HTTP " + str(e.code) + ": " + body[:500]})
        return 1
    except Exception as e:
        _emit({"ok": False, "error": "Network/runtime: " + repr(e)})
        return 1

    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        _emit({"ok": False, "error": "Unexpected API response shape: " + repr(e)})
        return 1

    try:
        review = json.loads(content)
    except json.JSONDecodeError as e:
        _emit({"ok": False, "error": "Model did not return JSON: " + str(e),
               "raw": content[:500]})
        return 1

    verdict = review.get("verdict")
    if verdict not in ("approve", "revise", "reject"):
        _emit({"ok": False, "error": "Unknown verdict: " + repr(verdict),
               "raw": content[:500]})
        return 1

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

    _emit({
        "ok": True,
        "verdict": verdict,
        "issues": issues,
        "rationale": str(review.get("rationale", "")),
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
