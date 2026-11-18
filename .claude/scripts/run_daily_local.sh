#!/bin/zsh
# Runs the /daily routine headlessly on this Mac (launchd calls this).
# Cloud routines can't be used: the Anthropic-hosted GitHub proxy blocks
# GraphQL, and Discussions are GraphQL-only.
set -u

REPO="$HOME/Projects/community-assistant"
LOG_DIR="$HOME/Library/Logs/community-assistant"
LOCK_DIR="${TMPDIR:-/tmp}/community-assistant-daily.lock"
MAX_SECONDS=2700   # a normal run takes 4-12 minutes; kill a hung one after 45
RUN_TMP=""
CHILD=""

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily-$(date +%Y%m%d-%H%M).log"

# Skip if a previous run is still going. The lock records its owner's PID, so
# a lock left behind by a killed run (or a reboot) is taken over, not obeyed.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  old_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "$(date) previous run (pid $old_pid) still active, skipping" >> "$LOG_DIR/skipped.log"
    exit 0
  fi
  echo "$(date) stale lock (pid ${old_pid:-unknown}), taking over" >> "$LOG_DIR/skipped.log"
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" || exit 1
fi
echo $$ > "$LOCK_DIR/pid"

cleanup() {
  [[ -n "$RUN_TMP" ]] && rm -rf "$RUN_TMP"
  rm -rf "$LOCK_DIR"
}
trap cleanup EXIT
# zsh does not run the EXIT trap when killed by a signal: exit explicitly,
# taking the agent down with us.
on_signal() {
  [[ -n "$CHILD" ]] && kill "$CHILD" 2>/dev/null
  exit "$1"
}
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

cd "$REPO" || exit 1

# Secrets live in the gitignored, chmod-600 .env: OPENAI_API_KEY for the
# review gate and, optionally, GH_TOKEN (a fine-grained token that can only
# write issues in this repo - see README). `set -a` exports them whether or
# not the file says `export`.
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

# Per-run temp dir: the wrapper owns cleanup.
RUN_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ca-run.XXXXXX")"

# The agent gets no direct `gh` at all. Every GitHub call goes through the
# scripts below, whose only write is `gh issue create` on this repo.
{
  echo "=== $(date) start"
  perl -e 'alarm shift; exec @ARGV or die "exec failed: $!\n"' "$MAX_SECONDS" \
    claude -p "/daily — use $RUN_TMP as the run directory (discover.py --out, draft and review files); the wrapper deletes it after the run, so skip cleanup." \
      --permission-mode auto \
      --allowedTools "Read" "Write" "Glob" "Grep" \
        "Bash(python3 .claude/scripts/discover.py:*)" \
        "Bash(python3 .claude/scripts/review_draft.py:*)" \
        "Bash(python3 .claude/scripts/publish_issue.py:*)" \
        "Bash(python3 .claude/scripts/mark_seen.py:*)" \
      --disallowedTools "Bash(gh:*)" "Bash(git push:*)" "Bash(git commit:*)" \
    < /dev/null &
  CHILD=$!
  wait "$CHILD"
  rc=$?
  CHILD=""
  (( rc == 142 )) && echo "=== killed: exceeded ${MAX_SECONDS}s"
  echo "=== $(date) exit $rc"
} >> "$LOG" 2>&1

# Make failures visible: a non-zero exit would otherwise sit unread in a log.
if (( rc != 0 )); then
  echo "$(date) exit $rc — see $LOG" >> "$LOG_DIR/failed.log"
  osascript -e "display notification \"Run failed (exit $rc). See $LOG\" with title \"community-assistant\"" 2>/dev/null || true
fi

# Keep the last 60 logs.
ls -1t "$LOG_DIR"/daily-*.log 2>/dev/null | tail -n +61 | xargs -r rm -f

exit "$rc"
