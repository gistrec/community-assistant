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
3. Creates **one issue per run** in this repo summarising the day's drafts
   (title `Daily discussion drafts — YYYY-MM-DD`).

## Setup

```bash
gh auth login          # GitHub CLI must be authenticated
git remote add origin git@github.com:gistrec/community-assistant.git
git push -u origin main
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
| `CLAUDE.md` | Project rules, target topics, discussion-handling behavior. Auto-loaded by Claude Code. |
| `.claude/commands/daily.md` | `/daily` slash command — the routine itself. |
