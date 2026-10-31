#!/bin/zsh
# Runs the /daily routine headlessly on this Mac (launchd calls this).
# Cloud routines can't be used: the Anthropic-hosted GitHub proxy blocks
# GraphQL, and Discussions are GraphQL-only.
set -u

REPO="$HOME/Projects/community-assistant"
LOG_DIR="$HOME/Library/Logs/community-assistant"
LOCK_DIR="${TMPDIR:-/tmp}/community-assistant-daily.lock"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily-$(date +%Y%m%d-%H%M).log"

# Skip if a previous run is still going.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date) previous run still active, skipping" >> "$LOG_DIR/skipped.log"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

cd "$REPO" || exit 1

# OPENAI_API_KEY for the review gate lives in the gitignored, chmod-600 .env.
if [[ -f .env ]]; then
  source .env
fi

# Per-run temp dir: the agent can't delete files (rm isn't allowed), so the
# wrapper owns cleanup.
RUN_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ca-run.XXXXXX")"
trap 'rm -rf "$RUN_TMP"; rmdir "$LOCK_DIR"' EXIT

{
  echo "=== $(date) start"
  claude -p "/daily — put every temp file under $RUN_TMP; the wrapper deletes it after the run, so skip cleanup." \
    --permission-mode auto \
    --allowedTools "Read" "Write" "Glob" "Grep" \
      "Bash(gh api graphql:*)" "Bash(gh issue list:*)" "Bash(gh issue create:*)" \
      "Bash(python3 .claude/scripts/review_draft.py:*)" "Bash(mktemp:*)" \
    --disallowedTools "Bash(git push:*)" "Bash(gh pr:*)" "Bash(gh api -X POST:*)" \
      "Bash(gh api --method:*)"
  echo "=== $(date) exit $?"
} >> "$LOG" 2>&1

# Keep the last 60 logs.
ls -1t "$LOG_DIR"/daily-*.log 2>/dev/null | tail -n +61 | xargs -r rm -f
