"""Shared helpers for the community-assistant scripts.

Everything that talks to GitHub goes through ``gh`` here. The only write
call in the whole code base is ``gh issue create -R gistrec/community-assistant``
in ``publish_issue.py``; every GraphQL document in these scripts is a query.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO = "gistrec/community-assistant"
SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent.parent

MAX_AGE_DAYS = 30
MAX_COMMENTS = 5

# Cache reasons. Permanent ones never resurface; the others resurface when
# the thread changes (new comment / reply, or OP edits the body).
PERMANENT_REASONS = (
    "answered",         # someone already attempted an answer, or it is solved
    "off_topic",        # not one of the target topics
    "not_a_question",   # bug report, feature request, maintainer-only question
    "skip_domain",      # legal / security-sensitive / medical / financial / personal
    "promo",            # the only useful answer would be promotional
    "review_rejected",  # the review gate rejected the draft
    "published",        # an issue was created
)
RECHECK_REASONS = (
    "needs_info",       # too vague to answer until OP adds details
    "low_confidence",   # answering would need guesswork
)
REASONS = PERMANENT_REASONS + RECHECK_REASONS

DISCUSSION_URL_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/discussions/([0-9]+)"
)

DISCUSSION_FRAGMENT = """
fragment D on Discussion {
  url
  number
  title
  bodyText
  createdAt
  updatedAt
  lastEditedAt
  closed
  locked
  isAnswered
  answerChosenAt
  answer { id }
  author { login }
  category { name isAnswerable }
  repository { nameWithOwner stargazerCount }
  comments(first: 10) {
    totalCount
    nodes {
      bodyText
      createdAt
      isAnswer
      author { login }
      replies(first: 10) {
        totalCount
        nodes { bodyText createdAt author { login } }
      }
    }
  }
}
"""


class GhError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# gh / GraphQL


def run_gh(args, timeout=90):
    try:
        p = subprocess.run(["gh"] + list(args), capture_output=True, text=True,
                           timeout=timeout)
    except FileNotFoundError:
        raise GhError("gh CLI not found on PATH")
    except subprocess.TimeoutExpired:
        raise GhError("gh timed out after " + str(timeout) + "s")
    return p.returncode, p.stdout, p.stderr


def graphql(query, variables=None, retries=1):
    """Run a read-only GraphQL query. Returns ``(data, errors)``.

    ``gh`` exits non-zero when the response carries ``errors`` even if
    ``data`` is partially filled (e.g. one repo of a batch was renamed), so
    stdout is parsed regardless of the exit code.
    """
    if re.search(r"\bmutation\b", query):
        raise GhError("refusing to send a GraphQL mutation")
    args = ["api", "graphql", "-f", "query=" + query]
    for key, value in (variables or {}).items():
        if isinstance(value, int) and not isinstance(value, bool):
            args += ["-F", key + "=" + str(value)]
        else:
            args += ["-f", key + "=" + str(value)]

    last = "unknown error"
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(5 * attempt)
        try:
            _code, out, err = run_gh(args)
        except GhError as e:
            last = str(e)
            continue
        try:
            doc = json.loads(out) if out.strip() else {}
        except ValueError:
            doc = {}
        data = doc.get("data") if isinstance(doc, dict) else None
        if data:
            return data, (doc.get("errors") or [])
        last = (err or out or "empty response").strip()[:300]
    raise GhError(last)


def tracked_urls():
    """Discussion URLs already reported in this repo's issues (any state)."""
    code, out, err = run_gh(
        ["issue", "list", "-R", REPO, "--state", "all", "--limit", "2000",
         "--json", "body"],
        timeout=120,
    )
    if code != 0:
        raise GhError("gh issue list failed: " + err.strip()[:300])
    urls = set()
    for issue in json.loads(out or "[]"):
        for m in DISCUSSION_URL_RE.finditer(issue.get("body") or ""):
            urls.add(m.group(0).lower())
    return urls


# --------------------------------------------------------------------------
# Discussion normalisation


def _login(node):
    return ((node or {}).get("author") or {}).get("login") or "(ghost)"


def is_bot(login):
    return login.endswith("[bot]") or login.endswith("-bot") or login in (
        "github-actions", "dependabot", "renovate")


def normalize(node, repo=None, stars=None):
    """GraphQL Discussion node -> flat candidate dict."""
    repository = node.get("repository") or {}
    comments_conn = node.get("comments") or {}
    comments = []
    reply_total = 0
    for c in comments_conn.get("nodes") or []:
        if not c:
            continue
        replies_conn = c.get("replies") or {}
        reply_total += replies_conn.get("totalCount") or 0
        comments.append({
            "author": _login(c),
            "body": c.get("bodyText") or "",
            "created_at": c.get("createdAt"),
            "is_answer": bool(c.get("isAnswer")),
            "replies": [
                {"author": _login(r), "body": r.get("bodyText") or "",
                 "created_at": r.get("createdAt")}
                for r in (replies_conn.get("nodes") or []) if r
            ],
        })
    category = node.get("category") or {}
    top = comments_conn.get("totalCount") or 0
    cand = {
        "url": node.get("url"),
        "repo": repo or repository.get("nameWithOwner"),
        "stars": stars if stars is not None else (repository.get("stargazerCount") or 0),
        "number": node.get("number"),
        "title": node.get("title") or "",
        "author": _login(node),
        "created_at": node.get("createdAt"),
        "updated_at": node.get("updatedAt"),
        "edited_at": node.get("lastEditedAt"),
        "category": category.get("name") or "",
        "answerable": bool(category.get("isAnswerable")),
        "closed": bool(node.get("closed")),
        "locked": bool(node.get("locked")),
        "answered": bool(node.get("isAnswered") or node.get("answer")
                         or node.get("answerChosenAt")),
        "top_comments": top,
        "comment_total": top + reply_total,
        "body": node.get("bodyText") or "",
        "comments": comments,
    }
    op = cand["author"]
    others = set()
    for c in comments:
        for entry in [c] + c["replies"]:
            who = entry["author"]
            if who != op and not is_bot(who):
                others.add(who)
    cand["other_commenters"] = len(others)
    return cand


def signature(cand):
    """Changes whenever the thread gets a comment/reply or OP edits the body."""
    return str(cand.get("comment_total", 0)) + ":" + str(cand.get("edited_at") or "")


def fetch_discussion(url):
    """Fresh snapshot of one discussion (used right before publishing)."""
    m = DISCUSSION_URL_RE.match(url or "")
    if not m:
        raise GhError("not a discussion URL: " + repr(url))
    query = DISCUSSION_FRAGMENT + """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { discussion(number: $number) { ...D } }
}"""
    data, _ = graphql(query, {"owner": m.group(1), "name": m.group(2),
                              "number": int(m.group(3))})
    node = ((data.get("repository") or {}).get("discussion"))
    if not node:
        raise GhError("discussion not found: " + url)
    return normalize(node)


def load_candidates(path):
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("candidates") or []


def find_candidate(candidates, url):
    wanted = (url or "").strip().lower().rstrip("/")
    for cand in candidates:
        if (cand.get("url") or "").lower() == wanted:
            return cand
    return None


# --------------------------------------------------------------------------
# Seen-cache


def state_dir():
    return Path(os.environ.get("CA_STATE_DIR") or (ROOT / ".cache"))


def cache_path():
    return state_dir() / "seen.json"


def load_cache():
    try:
        with open(cache_path(), "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    # Threads older than the freshness window are filtered anyway.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS + 15)).isoformat()
    pruned = {u: e for u, e in cache.items()
              if (e.get("created_at") or e.get("marked_at") or "9") >= cutoff[:10]}
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="seen.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def cache_mark(cache, cand, reason, note=""):
    entry = {
        "reason": reason,
        "sig": signature(cand),
        "created_at": cand.get("created_at"),
        "marked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if note:
        entry["note"] = note[:200]
    cache[(cand.get("url") or "").lower()] = entry


def cache_hides(cache, cand):
    """True when a cached decision still applies to this candidate."""
    entry = cache.get((cand.get("url") or "").lower())
    if not entry:
        return False
    if entry.get("reason") in PERMANENT_REASONS:
        return True
    return entry.get("sig") == signature(cand)


# --------------------------------------------------------------------------
# CLAUDE.md is the source of truth for seed repos and target topics


def claude_md_bullets(heading):
    """Bullet lines under ``## <heading>`` in CLAUDE.md (until the next ``## ``)."""
    try:
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"^## " + re.escape(heading) + r"\s*$(.*?)(?=^## |\Z)", text,
                  re.M | re.S)
    if not m:
        return []
    bullets, current = [], None
    for line in m.group(1).splitlines():
        if line.startswith("- "):
            if current:
                bullets.append(current)
            current = line[2:].strip()
        elif current and line.startswith("  ") and line.strip():
            current += " " + line.strip()
        else:
            if current:
                bullets.append(current)
            current = None
    if current:
        bullets.append(current)
    return bullets


def seed_repos():
    repos = []
    for bullet in claude_md_bullets("Seed repositories"):
        m = re.match(r"`([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)`$", bullet)
        if m:
            repos.append(m.group(1))
    return repos


# --------------------------------------------------------------------------
# Bare @mention lint

_PROTECTED_RE = re.compile(
    r"(?P<fence>^[ \t]*(?:```|~~~).*?(?:^[ \t]*(?:```|~~~)[ \t]*$|\Z))"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<link>\[@[A-Za-z0-9-]+\]\(https://github\.com/[A-Za-z0-9-]+/?\))",
    re.M | re.S,
)
_MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9_`/.])@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:/[A-Za-z0-9._-]+)?"
)


def _split_protected(text):
    """Yield ``(protected, chunk)``: code fences, code spans and link-form
    mentions are protected; GitHub never turns those into notifications."""
    pos = 0
    for m in _PROTECTED_RE.finditer(text):
        if m.start() > pos:
            yield False, text[pos:m.start()]
        yield True, m.group(0)
        pos = m.end()
    if pos < len(text):
        yield False, text[pos:]


def find_bare_mentions(text):
    found = []
    for protected, chunk in _split_protected(text or ""):
        if not protected:
            found.extend(_MENTION_RE.findall(chunk))
    return sorted(set(found))


def neutralize_mentions(text):
    """Wrap every bare ``@name`` in a code span so it cannot notify anyone."""
    out = []
    for protected, chunk in _split_protected(text or ""):
        out.append(chunk if protected
                   else _MENTION_RE.sub(lambda m: "`" + m.group(0) + "`", chunk))
    return "".join(out)


def sha256_text(text):
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))
