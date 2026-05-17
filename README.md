# Community Assistant

A scheduled Claude Code routine that finds fresh, unanswered GitHub
Discussions on selected technical topics, drafts possible replies, and surfaces
them as issues in this repo for me to review and post manually.

Drafts only — the routine never writes to other people's repos.

## How it works

`/daily` runs once a day in this repo:

1. Searches GitHub (via GraphQL) for discussions on the target topics in
   [`CLAUDE.md`](CLAUDE.md), restricted to:
   - **created** within the last **30 days**,
   - `closed = false` (the discussion is still open),
   - `isAnswered = false`, `answer = null`, `answerChosenAt = null`, and no
     comment that already provides an adequate answer.
2. Drafts a short, practical reply only where confidence is high and the
   draft adds something the existing comments don't already cover.
3. Creates one issue per draft (title `Draft: <owner/repo> — <discussion title>`)
   labelled `draft`, `github-discussion`, `needs-review`. URLs already tracked
   in this repo's open or closed issues are skipped.

## Setup

```bash
gh auth login          # GitHub CLI must be authenticated
git remote add origin git@github.com:gistrec/community-assistant.git
git push -u origin main

# Optional: pre-create labels (otherwise issues are created without labels).
gh label create draft             -R gistrec/community-assistant --color ededed
gh label create github-discussion -R gistrec/community-assistant --color 0e8a16
gh label create needs-review      -R gistrec/community-assistant --color fbca04

# Outcome labels — applied manually after posting the reply.
gh label create answer-accepted   -R gistrec/community-assistant --color 0e8a16
gh label create answer-rejected   -R gistrec/community-assistant --color b60205
```

Schedule via `/schedule` in Claude Code: cron `0 9 * * *`, prompt `/daily`,
working directory = this repo. To run on demand, type `/daily` from here.

## Layout

| Path | Purpose |
| --- | --- |
| `CLAUDE.md` | Rules, target topics, filters, output format. Auto-loaded by Claude Code. |
| `.claude/commands/daily.md` | `/daily` slash command — the routine itself. |
