# Community Assistant

A scheduled Claude Code routine that scans recent unanswered GitHub Discussions
on selected technical topics, drafts possible replies, and surfaces them as
issues in this repo for me to review and post manually.

The routine never posts on other people's repos. Only drafts.

## How it works

A daily scheduled routine runs `/daily` in this repository:

1. Searches GitHub (via GraphQL) for recent unanswered discussions in repos
   matching the target topics in [`CLAUDE.md`](CLAUDE.md).
2. Drafts a short, practical answer for each one where confidence is high.
3. Creates **one issue per draft** in this repo (title
   `Draft: <owner/repo> — <discussion title>`), labelled `draft`,
   `github-discussion`, `needs-review`. Discussions already reported in this
   repo's issues are skipped.

## Setup

```bash
gh auth login          # GitHub CLI must be authenticated
git remote add origin git@github.com:gistrec/community-assistant.git
git push -u origin main

# Optional: pre-create labels so the routine can attach them
# (otherwise issues are created without labels via fallback).
gh label create draft             -R gistrec/community-assistant --color ededed
gh label create github-discussion -R gistrec/community-assistant --color 0e8a16
gh label create needs-review      -R gistrec/community-assistant --color fbca04
```

Then schedule the routine. In Claude Code:

```
/schedule
```

Use `0 9 * * *` (daily 09:00) and the prompt:

```
/daily
```

with this directory as the working directory.

## Manual run

From this directory in Claude Code:

```
/daily
```

## Layout

| Path | Purpose |
| --- | --- |
| `CLAUDE.md` | Project rules, target topics, discussion-handling behavior, output format. Auto-loaded by Claude Code. |
| `.claude/commands/daily.md` | `/daily` slash command — the routine itself. |
