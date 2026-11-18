# Community Assistant

[![answers posted](https://img.shields.io/github/issues-search/gistrec/community-assistant?query=label%3Aposted%2Canswer-accepted%2Canswer-rejected%20is%3Aissue&label=answers%20posted&color=0e8a16)](https://github.com/gistrec/community-assistant/issues?q=label%3Aposted%2Canswer-accepted%2Canswer-rejected%20is%3Aissue)
[![accepted answers](https://img.shields.io/github/issues-search/gistrec/community-assistant?query=label%3Aanswer-accepted%20is%3Aissue&label=accepted&color=0e8a16)](https://github.com/gistrec/community-assistant/issues?q=label%3Aanswer-accepted%20is%3Aissue)

A scheduled Claude Code routine that finds fresh, unanswered GitHub Discussions on selected technical topics, drafts possible replies, and surfaces them as issues in this repo for me to review and post manually.

Drafts only — the routine never writes to other people's repos.

## How it works

`/daily` runs three times a day in this repo:

1. Searches GitHub (via GraphQL, `discover.py`) for discussions on the target topics in [`CLAUDE.md`](CLAUDE.md), restricted to:
   - **created** within the last **30 days**,
   - `closed = false` (the discussion is still open),
   - `isAnswered = false`, `answer = null`, `answerChosenAt = null`, and no answer attempt from anyone but OP (comments and nested replies) — the draft must be the **first** substantive reply, so even a partial or unconfirmed attempt disqualifies the thread.

   Threads a previous run already triaged are remembered in a local seen-cache (`.cache/seen.json`) and not read again unless they change.
2. Drafts a short, practical reply where confidence is high or medium (medium drafts flag their uncertainty); low-confidence candidates are skipped.
3. Sends each draft through OpenAI Chat Completions (default model `gpt-5.5`, falling back to `gpt-5.4`; env `OPENAI_API_KEY`) for a pre-publish review. The review is advisory (since 2026-11-18): `revise` verdicts get the fixes applied and one re-review, and the issue records both rounds in a `## ChatGPT review` section; `reject` verdicts (either round) are still published, with the reviewer's reasons and the `review-rejected` label. If the API call fails (after one automatic retry of a transient error) the draft is published with a "not reviewed" note.
4. Creates one issue per surviving draft (title `Draft: <owner/repo> — <discussion title>`) labelled `draft`, `github-discussion`, `needs-review`. URLs already tracked in this repo's open or closed issues are skipped. Right before creating the issue `publish_issue.py` re-checks that the thread is still unanswered and that the body contains no bare `@name` that would notify someone.

## Setup

```bash
gh auth login          # GitHub CLI must be authenticated

# Secrets go into a gitignored .env (chmod 600); the launchd wrapper exports them.
echo 'OPENAI_API_KEY=…' >> .env   # required — drafts that can't be reviewed are dropped
echo 'GH_TOKEN=…'       >> .env   # optional — see "Least-privilege token" below
chmod 600 .env

git remote add origin git@github.com:gistrec/community-assistant.git
git push -u origin main

# Optional: pre-create labels (otherwise issues are created without labels).
gh label create draft             -R gistrec/community-assistant --color 5319e7
gh label create github-discussion -R gistrec/community-assistant --color 0366d6
gh label create needs-review      -R gistrec/community-assistant --color fbca04
gh label create review-rejected   -R gistrec/community-assistant --color e99695   # reviewer said "don't post"; never remove it — it is the record

# Review labels — applied manually when closing a draft issue.
gh label create posted            -R gistrec/community-assistant --color 0e8a16
gh label create skipped           -R gistrec/community-assistant --color d93f0b
gh label create stale             -R gistrec/community-assistant --color bfdadc

# Outcome labels — added on top of `posted` once OP reacts.
gh label create answer-accepted   -R gistrec/community-assistant --color 0e8a16
gh label create answer-rejected   -R gistrec/community-assistant --color b60205
```

Label lifecycle (the README badges count these labels):

1. When closing a draft issue, replace `needs-review` with exactly one of `posted` (reply is live) or `skipped` (not posted); add `stale` if the thread went stale.
2. Never remove `posted`. Outcome labels go on top of it: add `answer-accepted` when OP marks the reply as the answer (even after the issue is closed), or `answer-rejected` if OP turns it down.

Runs locally via launchd at 05:00, 13:00 and 21:00 (three runs a day because unanswered threads usually get a first reply within hours). Cloud routines can't be used: the Anthropic-hosted GitHub proxy blocks GraphQL, and Discussions are GraphQL-only. Install: `mkdir -p ~/Library/Logs/community-assistant && cp .claude/scripts/com.gistrec.community-assistant.daily.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.gistrec.community-assistant.daily.plist`. Logs go to `~/Library/Logs/community-assistant/`; a failed run (non-zero exit, or killed after 45 minutes) is also appended to `failed.log` there and raises a macOS notification. The wrapper `.claude/scripts/run_daily_local.sh` sources the gitignored `.env`. To run on demand, type `/daily` from here.

The headless run signs in with your Claude account. An interactive sign-in expires; for an unattended job create a long-lived token with `claude setup-token`.

### Least-privilege token (optional, recommended)

The agent can't call `gh` directly — the wrapper denies it, and the scripts' only write is `gh issue create` on this repo. To enforce the same thing on GitHub's side, give the routine its own fine-grained personal access token instead of your `gh auth login` session:

1. GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token.
2. Repository access: **Only select repositories** → `gistrec/community-assistant`.
3. Permissions: **Issues: Read and write** (Metadata: Read is added automatically). Nothing else.
4. Add it to `.env` as `GH_TOKEN=…`. `gh` picks `GH_TOKEN` up from the environment.
5. Check that it can still read other repos' public discussions before relying on it: `set -a; source .env; set +a; python3 .claude/scripts/discover.py` should report `"ok": true` with a non-zero `merged` count and an empty `errors` list. If it doesn't, remove the line again.

## Layout

| Path | Purpose |
| --- | --- |
| `CLAUDE.md` | Rules, target topics, filters, output format. Auto-loaded by Claude Code. |
| `.claude/commands/daily.md` | `/daily` slash command — the routine itself. |
| `.claude/scripts/discover.py` | Discovery, mechanical filters, dedup, seen-cache. Read-only. |
| `.claude/scripts/discovery.json` | Search keywords, topic/language pairs, skipped categories. |
| `.claude/scripts/review_draft.py` | Pre-publish ChatGPT review, advisory (called from `/daily`). |
| `.claude/scripts/publish_issue.py` | Review record + `@mention` lint + live re-check, then `gh issue create` — the only write. |
| `.claude/scripts/mark_seen.py` | Records triage decisions in `.cache/seen.json`. |
| `.claude/scripts/ca_common.py` | Shared helpers (GraphQL, cache, lint). |
| `.claude/scripts/run_daily_local.sh` | launchd wrapper: lock, timeout, secrets, permissions, logs. |
| `.claude/scripts/com.gistrec.community-assistant.daily.plist` | launchd schedule. |
