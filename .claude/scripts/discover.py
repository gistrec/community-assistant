#!/usr/bin/env python3
"""Discovery + mechanical filtering for the /daily routine (steps 1-3).

Runs the three discovery paths (global discussion search, seed repos,
topic-based repo discovery), merges by URL and applies every filter that
needs no judgment: age, answered / closed / locked, category, registry
submissions, busy threads, URLs already tracked in this repo's issues, and
the seen-cache of earlier triage decisions.

Usage::

    python3 .claude/scripts/discover.py [--out DIR] [--keyword KW ...]

Writes into DIR (a fresh temp dir when omitted):

    candidates.json   full data, consumed by review_draft.py / publish_issue.py
    index.md          one line per candidate - read this first
    c/NNN.md          body + comments + replies of candidate NNN

Prints a single-line JSON summary (paths, counts, filter stats, errors).
Exit code 1 only when discovery produced nothing because every query failed.
Read-only: GraphQL queries plus ``gh issue list`` on this repo.
"""

import argparse
import json
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ca_common as ca


SEARCH_QUERY = ca.DISCUSSION_FRAGMENT + """
query($q: String!) {
  search(query: $q, type: DISCUSSION, first: 50) { nodes { ...D } }
}"""

REPO_SEARCH_QUERY = """
query($q: String!) {
  search(query: $q, type: REPOSITORY, first: 30) {
    nodes { ... on Repository { nameWithOwner stargazerCount hasDiscussionsEnabled } }
  }
}"""

REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BATCH = 5
BODY_LIMIT = 8000
COMMENT_LIMIT = 2500


def search_discussions(keyword, since):
    base = keyword + " is:unanswered is:open created:>=" + since
    data, _ = ca.graphql(SEARCH_QUERY, {"q": base + " comments:0"})
    nodes = [n for n in (data["search"]["nodes"] or []) if n]
    if len(nodes) < 5:
        # Nearly empty keyword: widen once and let the triage read comments.
        data, _ = ca.graphql(SEARCH_QUERY, {"q": base + " comments:<=3"})
        nodes = [n for n in (data["search"]["nodes"] or []) if n]
    return nodes


def search_repos(pair, since):
    q = pair + " pushed:>=" + since + " sort:stars-desc"
    data, _ = ca.graphql(REPO_SEARCH_QUERY, {"q": q})
    return [n for n in (data["search"]["nodes"] or [])
            if n and n.get("hasDiscussionsEnabled")]


def scan_repos(repos):
    """Unanswered discussions of up to BATCH repos in one aliased query."""
    parts = []
    for i, repo in enumerate(repos):
        owner, name = repo.split("/", 1)
        parts.append(
            'r%d: repository(owner: "%s", name: "%s") { nameWithOwner '
            "hasDiscussionsEnabled discussions(first: 20, answered: false, "
            "orderBy: {field: CREATED_AT, direction: DESC}) { nodes { ...D } } }"
            % (i, owner, name)
        )
    data, errors = ca.graphql(ca.DISCUSSION_FRAGMENT + "query {\n" + "\n".join(parts) + "\n}")
    nodes, notes = [], []
    for i, repo in enumerate(repos):
        r = data.get("r%d" % i)
        if not r:
            notes.append(repo + ": not found (renamed or removed?)")
        elif not r.get("hasDiscussionsEnabled"):
            notes.append(repo + ": discussions disabled")
        else:
            nodes.extend(n for n in (r["discussions"]["nodes"] or []) if n)
    return nodes, notes


def chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def clip(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def one_line(text, limit):
    s = " ".join((text or "").split()).replace("|", "/")
    return s if len(s) <= limit else s[:limit - 1] + "…"


def render_candidate(n, c):
    op = c["author"]
    lines = [
        "# " + c["title"],
        "",
        "- n: %03d" % n,
        "- url: " + c["url"],
        "- repo: %s (★%d)" % (c["repo"], c["stars"]),
        "- category: " + c["category"],
        "- author (OP): " + op,
        "- created: %s | updated: %s" % (c["created_at"], c["updated_at"]),
        "- comments: %d top-level, %d incl. replies, %d other commenter(s)"
        % (c["top_comments"], c["comment_total"], c["other_commenters"]),
        "- found via: " + ", ".join(c["sources"]),
        "",
        "## Body",
        "",
        clip(c["body"], BODY_LIMIT),
        "",
        "## Comments",
        "",
    ]
    if not c["comments"]:
        lines.append("(none)")
    for comment in c["comments"]:
        tag = " (OP)" if comment["author"] == op else ""
        lines += ["### %s%s — %s" % (comment["author"], tag, comment["created_at"]),
                  "", clip(comment["body"], COMMENT_LIMIT), ""]
        for reply in comment["replies"]:
            tag = " (OP)" if reply["author"] == op else ""
            lines += ["  ↳ reply by %s%s:" % (reply["author"], tag),
                      "  " + clip(reply["body"], COMMENT_LIMIT).replace("\n", "\n  "), ""]
    if c["top_comments"] > len(c["comments"]):
        lines.append("(%d more comment(s) not fetched)" % (c["top_comments"] - len(c["comments"])))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="output directory (default: fresh temp dir)")
    ap.add_argument("--keyword", action="append", default=[],
                    help="extra search keyword for this run (repeatable)")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the seen-cache (show everything again)")
    args = ap.parse_args()

    with open(ca.SCRIPTS_DIR / "discovery.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="ca-discover."))
    (out / "c").mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ca.MAX_AGE_DAYS)
    since = cutoff.strftime("%Y-%m-%d")
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    errors, notes = [], []
    merged = {}   # url -> (node, set of sources)

    def add(nodes, source):
        for node in nodes:
            url = node.get("url")
            if not url:
                continue
            if url in merged:
                merged[url][1].add(source)
            else:
                merged[url] = (node, {source})

    keywords = list(dict.fromkeys(cfg["keywords"] + args.keyword))
    seeds = [r for r in ca.seed_repos() if REPO_NAME_RE.match(r)]
    if not seeds:
        notes.append("no seed repos parsed from CLAUDE.md")

    def guarded(label, fn, *fn_args):
        try:
            return fn(*fn_args)
        except Exception as e:   # one failed query must not sink the run
            errors.append(label + ": " + str(e)[:200])
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        kw_jobs = [(kw, pool.submit(guarded, "search " + kw, search_discussions, kw, since))
                   for kw in keywords]
        seed_jobs = [pool.submit(guarded, "seeds " + ",".join(b), scan_repos, b)
                     for b in chunks(seeds, BATCH)]
        repo_jobs = [(pair, pool.submit(guarded, "repos " + pair, search_repos, pair, since))
                     for pair in cfg["repo_searches"]]

        for kw, job in kw_jobs:
            add(job.result() or [], "search:" + kw)
        for job in seed_jobs:
            res = job.result()
            if res:
                add(res[0], "seed")
                notes.extend(res[1])

        skip_repos = {r.lower() for r in seeds + cfg.get("exclude_repos", [])}
        discovered = []
        for pair, job in repo_jobs:
            taken = 0
            for repo in job.result() or []:
                name = repo["nameWithOwner"]
                if name.lower() in skip_repos or not REPO_NAME_RE.match(name):
                    continue
                skip_repos.add(name.lower())
                discovered.append(name)
                taken += 1
                if taken >= cfg.get("repos_per_search", 5):
                    break
        discovered = discovered[:cfg.get("max_discovered_repos", 40)]
        topic_jobs = [pool.submit(guarded, "topic repos " + ",".join(b), scan_repos, b)
                      for b in chunks(discovered, BATCH)]
        for job in topic_jobs:
            res = job.result()
            if res:
                add(res[0], "topic-repo")

    total_queries = len(keywords) + len(cfg["repo_searches"]) + len(chunks(seeds, BATCH))
    if not merged and errors and len(errors) >= total_queries:
        ca.emit({"ok": False, "error": "every discovery query failed",
                 "errors": errors[:5]})
        return 1

    try:
        tracked = ca.tracked_urls()
    except ca.GhError as e:
        # Without the dedup set we could re-report threads: fail closed.
        ca.emit({"ok": False, "error": "could not load tracked URLs: " + str(e)})
        return 1

    cache = {} if args.no_cache else ca.load_cache()
    patterns = [p.lower() for p in cfg["skip_category_patterns"]]
    stats = {k: 0 for k in ("too_old", "answered_or_closed", "locked", "category",
                            "registry", "busy", "tracked", "seen")}
    survivors = []

    for url, (node, sources) in merged.items():
        c = ca.normalize(node)
        c["sources"] = sorted(sources)
        category = c["category"].lower()
        title = c["title"].lower()
        if (c["created_at"] or "") < cutoff_iso:
            stats["too_old"] += 1
        elif c["answered"] or c["closed"]:
            stats["answered_or_closed"] += 1
        elif c["locked"]:
            stats["locked"] += 1
        elif not c["answerable"] or any(p in category for p in patterns):
            stats["category"] += 1
        elif any((c["repo"] or "").lower() == rule["repo"].lower()
                 and any(t in title for t in rule["title_contains"])
                 for rule in cfg.get("skip_title_rules", [])):
            stats["registry"] += 1
        elif c["top_comments"] > ca.MAX_COMMENTS:
            stats["busy"] += 1
        elif url.lower() in tracked:
            stats["tracked"] += 1
        elif ca.cache_hides(cache, c):
            stats["seen"] += 1
        else:
            survivors.append(c)

    # Zero-comment threads first, then bigger repos first.
    survivors.sort(key=lambda c: (c["comment_total"] > 0, -c["stars"], c["url"]))

    index = [
        "# Candidates (%d) — %s" % (len(survivors), now.strftime("%Y-%m-%d %H:%M UTC")),
        "",
        "n | repo ★stars | category | comments(others) | created | title | snippet",
        "Details for candidate n: c/NNN.md in this directory.",
        "",
    ]
    for n, c in enumerate(survivors, 1):
        c["n"] = n
        (out / "c" / ("%03d.md" % n)).write_text(render_candidate(n, c), encoding="utf-8")
        index.append("%03d | %s ★%d | %s | %d(%d) | %s | %s | %s" % (
            n, c["repo"], c["stars"], one_line(c["category"], 24),
            c["comment_total"], c["other_commenters"], (c["created_at"] or "")[:10],
            one_line(c["title"], 90), one_line(c["body"], 110)))
    (out / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")

    with open(out / "candidates.json", "w", encoding="utf-8") as f:
        json.dump({"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "candidates": survivors}, f, ensure_ascii=False)

    ca.emit({
        "ok": True,
        "dir": str(out),
        "index": str(out / "index.md"),
        "candidates_file": str(out / "candidates.json"),
        "candidates": len(survivors),
        "zero_comment": sum(1 for c in survivors if c["comment_total"] == 0),
        "merged": len(merged),
        "filtered": stats,
        "scanned": {"keywords": len(keywords), "seed_repos": len(seeds),
                    "repo_searches": len(cfg["repo_searches"]),
                    "topic_repos": len(discovered)},
        "notes": notes[:10],
        "errors": errors[:10],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
