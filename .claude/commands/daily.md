---
description: Find GitHub Discussions on target topics with no direct answer yet and draft first replies as issues in this repo
---

Run the daily Community Assistant routine.

Read `CLAUDE.md` first — rules, target topics, discussion-handling behavior,
and tooling rules live there. The flow below executes those rules; it does
not override them.

## Steps

### 1. Gather candidate discussions

Use **all three** discovery paths below and merge the results (dedup by
discussion URL). Don't rely on any single one — each catches threads the
others miss.

Let `__SINCE__` = `today − 30d`, ISO date (matches the 30-day freshness
filter in step 3).

#### 1a. Global discussion search (primary)

Search discussions across all of GitHub by topical keywords. This is the
widest net and doesn't require knowing repos ahead of time.

```bash
gh api graphql -f query='
query($q: String!) {
  search(query: $q, type: DISCUSSION, first: 50) {
    nodes {
      ... on Discussion {
        url
        title
        bodyText
        author { login }
        createdAt
        updatedAt
        closed
        isAnswered
        answer { bodyText author { login } createdAt }
        answerChosenAt
        category { name isAnswerable }
        repository { nameWithOwner stargazerCount }
        comments(first: 20) {
          totalCount
          nodes { bodyText author { login } createdAt isAnswer }
        }
      }
    }
  }
}' -f q="<keywords> is:unanswered is:open created:>=__SINCE__ comments:0"
```

Run this once per topic keyword from `CLAUDE.md` → "Target topics" (both
primary **and** "also welcome"). Suggested keyword set: `cmake`, `vcpkg`,
`conan`, `pybind11`, `cpp`, `c++`, `modern c++`, `python packaging`,
`pyproject`, `setuptools`, `poetry`, `github actions`.

Query notes:

- Keep `comments:0` in every query — it filters server-side to threads
  nobody has replied to yet, which is exactly what we draft for (first
  answers). If a keyword comes back nearly empty, retry it once with
  `comments:<=3` and triage the comments locally in step 3.
- Do **not** add `language:` to DISCUSSION searches — it silently returns
  zero results (observed 2026-07-02). Narrow an overly broad keyword with a
  second term (e.g. `cpp linker`) instead.

Treat `is:unanswered` as best-effort — still apply the defensive check in
step 3 (`isAnswered == false`, `answer == null`, `answerChosenAt == null`).

#### 1b. Seed repository scan (always run)

For every repo listed in `CLAUDE.md` → "Seed repositories", fetch recent
unanswered discussions directly. This guarantees coverage of high-signal
projects regardless of what search returns.

```bash
gh api graphql -f query='
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    hasDiscussionsEnabled
    discussions(
      first: 20
      answered: false
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      nodes {
        url
        title
        bodyText
        author { login }
        createdAt
        updatedAt
        closed
        isAnswered
        answer { bodyText author { login } createdAt }
        answerChosenAt
        category { name isAnswerable }
        comments(first: 20) {
          totalCount
          nodes { bodyText author { login } createdAt isAnswer }
        }
      }
    }
  }
}' -f owner="<owner>" -f name="<name>"
```

Skip the repo if `hasDiscussionsEnabled = false` (the seed list can drift).

#### 1c. Topic-based repo discovery (fallback)

As a third path, find additional active repos via repository search and then
pull their unanswered discussions with the same query as 1b.

```bash
gh api graphql -f query='
query($q: String!) {
  search(query: $q, type: REPOSITORY, first: 50) {
    nodes {
      ... on Repository {
        nameWithOwner
        stargazerCount
        hasDiscussionsEnabled
        pushedAt
      }
    }
  }
}' -f q="topic:cpp language:C++ pushed:>=__SINCE__ sort:stars-desc"
```

Run this for each topic with both `topic:` and `language:` qualifiers
combined (per `CLAUDE.md` — `topic:` alone is too narrow). Paginate past 50
if results are sparse. Keep only repos with `hasDiscussionsEnabled = true`,
then fetch their discussions with the 1b query.

### 2. Merge and dedup

Merge nodes from 1a, 1b, 1c into a single candidate list keyed by discussion
`url`. Carry forward `repository.nameWithOwner` (1a provides it inline; for
1b/1c set it from the source repo). Do not re-fetch a discussion you already
have from another path.

### 3. Filter

Keep a discussion only if **all** of the following hold:

- Defensive sanity check: `isAnswered == false`, `answer == null`,
  `answerChosenAt == null`, `closed == false`. The `answered: false`
  connection arg is the primary cut for unanswered; this catches edge cases
  (re-categorized threads, manual unmarks, closed-but-unanswered threads).
- **Not already reported.** Fetch existing issues in
  `gistrec/community-assistant` once at the start of this step and skip any
  candidate whose URL appears in an existing body (open or closed):

  ```bash
  gh issue list -R gistrec/community-assistant \
    --state all --limit 200 \
    --json title,body,url
  ```
- `category.isAnswerable == true`, and the category is not
  announcement / general / show-and-tell.
- `createdAt` is within the last 30 days.
- It is a real technical question mapping to a target topic in `CLAUDE.md`.
- It is not in a skip-domain (legal, security-sensitive, medical, financial,
  personal).
- **Comment triage (cheap pre-filter):** `comments.totalCount == 0` passes
  outright — nothing to read. If `totalCount > 5`, skip without reading:
  threads that busy nearly always contain an answer attempt. For 1–5
  comments, read them and apply the next two checks.
- After reading existing comments, it does **not** appear solved
  (see "Discussion handling" in `CLAUDE.md`).
- **No answer attempt yet** (first-answer rule — see "Discussion handling"
  in `CLAUDE.md`): no commenter other than OP has proposed a solution,
  explanation, workaround, or diagnosis, even a partial or unconfirmed one.
  Non-answers ("+1", bot notices, requests for more info, OP's own
  follow-ups) don't disqualify the thread.
- The draft would not mainly promote a tool, library, blog post, or my
  profile (see Rules in `CLAUDE.md`).

Sort survivors: zero-comment threads first, then by repo stargazer count
desc — bigger repos first.

### 4. Draft

Confidence:

- **high** — well-known, citeable answer; concise and correct.
- **medium** — reasonable but may miss context; flag uncertainty in the draft.
- **low** — skip.

Drafts: 3–10 lines, code only if it materially helps. No filler, no apologies,
no "great question".

### 5. Stop

Cap the run at **5 drafts total** and **1 draft per repo**. **Zero drafts is
acceptable** — never inflate to fill the quota. If nothing passes the bar,
print a short summary of what was checked (repos scanned, candidates seen,
why each was rejected) and end with `No drafts today.`.

### 6. Pre-publish review + revision round (ChatGPT)

For every surviving draft, run a review through OpenAI before turning it
into an issue (draft → review → apply fixes → one re-review → final
answer). This catches invented APIs, answer attempts in the
comments that step 3 missed, promotional drift, bare `@<login>` mentions,
and skip-domain slips. The review is a **gate** — `reject` verdicts are
dropped on the floor and never become issues.

**Auth.** Read `$OPENAI_API_KEY` from the environment. If it is unset or
empty, **skip every remaining draft** (fail-closed) and note the reason in
the final report. No verification, no issue.

**Build the input.** For each draft, use the `Write` tool to create a temp
JSON file with this shape (use data already in your candidate list — do not
re-fetch the discussion):

```json
{
  "discussion": {
    "title": "<title>",
    "url": "<url>",
    "topic": "<one of the target topics>",
    "body": "<bodyText>",
    "answer": "<answer.bodyText or null>",
    "comments": [
      {"author": "<login>", "body": "<bodyText>"}
    ]
  },
  "draft": "<the draft reply, exact text you'd put under ## Draft reply>"
}
```

**Run the script.**

```bash
python3 .claude/scripts/review_draft.py "$INPUT_FILE"
```

The script prints a single-line JSON result. On success:

```json
{
  "ok": true,
  "verdict": "approve" | "revise" | "reject",
  "issues": [
    {"severity": "low|medium|high", "category": "<tag>", "description": "<text>"}
  ],
  "rationale": "<1-2 sentences>",
  "model": "<model id used>"
}
```

On failure (missing key, network error, non-200 from OpenAI, malformed
model output):

```json
{"ok": false, "error": "<reason>"}
```

The script exits non-zero on failure. **Treat any failure as fail-closed:
drop the draft and record the error in the final report. Do not retry a
failed call.** Default model is `gpt-5.5` at `reasoning_effort: high`; the
script falls back to `gpt-5.4` when the account lacks flagship access.
Override via `OPENAI_REVIEW_MODEL` / `OPENAI_REVIEW_EFFORT` (e.g. `xhigh`
for the heaviest reviews).

**Apply the gate (with one revision round).**

- `verdict == "reject"` — drop the draft. Record
  `<repo> — <title>: rejected (<rationale>)` for the final report.
- `verdict == "approve"` — keep the draft as-is. No extra section in step 7.
- `verdict == "revise"` — apply the fixes, then re-review **once**:

  1. Rewrite the draft so every flagged issue is addressed. All drafting
     rules still apply (3–10 lines, no invented APIs, no bare `@<login>`,
     no promotion, first-answer policy).
  2. Build a fresh temp JSON with the same discussion payload and the
     revised draft, and run the script again. Round 2 is the **only**
     re-review — never loop further.
  3. Round 2 `approve` — publish the revised draft; in step 7 add the
     `## ChatGPT review` section recording both rounds.
  4. Round 2 `revise` — publish the revised draft; the step 7 section
     marks the remaining issues as outstanding for the maintainer.
  5. Round 2 `reject` — drop the draft. Record
     `<repo> — <title>: rejected on re-review (<rationale>)`.

Delete every temp input file after use.

### 7. Open issues (one per surviving draft)

Create one issue per draft that passed the gate (verdict `approve` or
`revise`). Title and body format come from `CLAUDE.md` → "Output format" —
do not improvise the format here.

For each surviving draft:

1. Build a body file (`mktemp`) with these fields and sections (match the
   template in `CLAUDE.md` → "Output format" exactly — link form is required
   for any GitHub login, never bare `@<login>`):
   - `**Discussion:** [<discussion title>](<url>)`
   - `**Repository:** [<owner/repo>](https://github.com/<owner/repo>)`
   - `**Author:** [<discussion author>](https://github.com/<discussion author>)`
   - `**Updated:** <discussion updatedAt>`
   - `**Topic:** <topic>`
   - `**Confidence:** high | medium`
   - `**Why useful:** <short reason>`
   - `## Context` — 1–3 sentences summarising the discussion and existing
     comments. Refer to commenters descriptively; never bare `@<login>`.
   - `## Draft reply` — the draft (3–10 lines). Same rule — if a login is
     unavoidable, use `[@<login>](https://github.com/<login>)`.
   - `## ChatGPT review` — **include only when the draft went through the
     step 6 revision round** (round 1 said `revise`). `## Draft reply`
     always holds the final revised text. Render as:

     ```markdown
     ## ChatGPT review

     - **Round 1:** revise — <round 1 rationale>
       - **(high)** [<category>] <description> — applied
       - **(medium)** [<category>] <description> — applied
     - **Round 2:** approve — <round 2 rationale>

     <details>
     <summary>Original draft (before review fixes)</summary>

     <the round 1 draft text>

     </details>
     ```

     One bullet per round 1 issue, preserving severity ordering
     (high → medium → low). If round 2 returned `revise`, list its issues
     under the Round 2 bullet marked `— outstanding` and add a closing
     line `Resolve the outstanding issues before posting.` Omit this whole
     section when round 1 already said `approve`.
   - `## Checklist` — three unchecked items per the CLAUDE.md template.

2. Create the issue:

   ```bash
   gh issue create \
     -R gistrec/community-assistant \
     --title "Draft: <owner/repo> — <discussion title>" \
     --body-file "$BODY_FILE" \
     --label "draft,github-discussion,needs-review"
   ```

3. **Label fallback.** If the call fails because the labels do not exist
   in the repo, retry the same `gh issue create` **without** `--label`. Do
   not auto-create labels — only attach them when they already exist.

4. Remove the temp file.

### 8. Report

Print a one-line summary per published draft
(`<repo> — <title> — <final verdict, rounds used, review model> — <url>`)
and the total count. Also list drafts rejected by the ChatGPT gate in
either round (`<repo> — <title>: rejected — <rationale>`) and any drafts
dropped due to review-call failure
(`<repo> — <title>: review failed — <error>`). If nothing was published,
end with `No drafts today.`.

## Hard rules

- Read-only `gh api graphql` on external repos. No mutation against any repo
  other than `gistrec/community-assistant`.
- **Never call `gh search discussions`** — that command does not exist.
- Never invent code, APIs, or compiler flags. If the answer requires guessing,
  mark confidence low and skip.
- Never include personal identifiers from the discussion (real names, emails)
  in the draft or issue body — the link is enough.
