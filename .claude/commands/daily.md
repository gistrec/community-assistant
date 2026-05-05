Run the daily Community Assistant routine.

Read `CLAUDE.md` first — rules, target topics, discussion-handling behavior,
and tooling rules live there. The flow below executes those rules; it does
not override them.

## Steps

### 1. Find candidate repositories

For each primary topic in `CLAUDE.md`, use `gh api graphql` to find active
repos with discussions enabled:

```bash
gh api graphql -f query='
query($q: String!) {
  search(query: $q, type: REPOSITORY, first: 25) {
    nodes {
      ... on Repository {
        nameWithOwner
        stargazerCount
        hasDiscussionsEnabled
        pushedAt
      }
    }
  }
}' -f q="topic:cpp pushed:>=__SINCE__ sort:stars-desc"
```

`__SINCE__` = `today − 30d`, ISO date.

Topic mapping:

- C++ → `topic:cpp` (and/or `language:C++`)
- Python → `topic:python` (and/or `language:Python`)
- CMake → `topic:cmake`

Keep only repos with `hasDiscussionsEnabled = true`.

### 2. List unanswered discussions per repo

For each candidate repo, fetch recent unanswered discussions with everything
needed to filter and draft. Filter at the API level via `answered: false`
(GitHub added the argument in October 2023):

```bash
gh api graphql -f query='
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    discussions(
      first: 20
      answered: false
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes {
        url
        title
        bodyText
        author { login }
        createdAt
        updatedAt
        isAnswered
        answer { bodyText author { login } createdAt }
        answerChosenAt
        category { name isAnswerable }
        comments(first: 20) {
          totalCount
          nodes {
            bodyText
            author { login }
            createdAt
            isAnswer
          }
        }
      }
    }
  }
}' -f owner="<owner>" -f name="<name>"
```

### 3. Filter

Keep a discussion only if **all** of the following hold:

- Defensive sanity check: `isAnswered == false`, `answer == null`,
  `answerChosenAt == null`. The `answered: false` connection arg is the
  primary cut; this catches edge cases (re-categorized threads, manual
  unmarks).
- `category.isAnswerable == true`, and the category is not
  announcement / general / show-and-tell.
- `updatedAt` is within the last 90 days.
- It is a real technical question mapping to a target topic in `CLAUDE.md`.
- It is not in a skip-domain (legal, security-sensitive, medical, financial,
  personal).
- After reading existing comments, it does **not** appear solved
  (see "Discussion handling" in `CLAUDE.md`).
- No existing comment already covers the answer you'd write — unless you have
  a clearly useful correction or missing detail.
- The draft would not mainly promote a tool, library, blog post, or my
  profile (see Rules in `CLAUDE.md`).

Sort survivors by repo stargazer count desc — bigger repos first.

### 4. Draft

Confidence:

- **high** — well-known, citeable answer; concise and correct.
- **medium** — reasonable but may miss context; flag uncertainty in the draft.
- **low** — skip.

Drafts: 3–10 lines, code only if it materially helps. No filler, no apologies,
no "great question". If adding to an existing comment, lead with what's new.

### 5. Stop

Cap the run at **5 drafts total** and **1 draft per repo**. If nothing passes
the bar, print `No drafts today.` and exit.

### 6. Open one issue

Create **exactly one** issue in this repo summarising the day's drafts. Use
the title and per-draft section format from `CLAUDE.md` → "Output format".
If no drafts passed the bar, skip this step entirely (already exited at
step 5 with `No drafts today.`).

Build the body by concatenating one section per draft, separated by `---`,
then create the issue:

```bash
TODAY=$(date -u +%F)
BODY_FILE=$(mktemp)

# For each draft, append a section to $BODY_FILE in the format from CLAUDE.md:
#   ## <owner/repo> — <discussion title>
#   - **Link:** ...
#   - **Topic:** ...
#   - **Confidence:** ...
#   - **Why useful:** ...
#
#   ### Draft
#
#   <answer text>
#
#   ---

gh issue create \
  -R gistrec/community-assistant \
  --title "Daily discussion drafts — $TODAY" \
  --body-file "$BODY_FILE"

rm -f "$BODY_FILE"
```

### 7. Report

Print a one-line summary per draft (`<repo> — <title> — <url>`) and the total
count.

## Hard rules

- Read-only `gh api graphql` on external repos. No mutation against any repo
  other than `gistrec/community-assistant`.
- **Never call `gh search discussions`** — that command does not exist.
- Never invent code, APIs, or compiler flags. If the answer requires guessing,
  mark confidence low and skip.
- Never include personal identifiers from the discussion (real names, emails)
  in the draft or issue body — the link is enough.
