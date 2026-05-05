# Community Assistant — project instructions

This repo runs a daily routine that finds unanswered GitHub Discussions on
selected technical topics and drafts possible replies for me to review.

## Rules

- **Never** post comments, issues, PRs, or discussions on other repos.
- The only allowed write is creating issues in **this repo**
  (`gistrec/community-assistant`).
- Only generate draft answers — I review and post manually.
- Prefer short, practical, technically accurate answers (3–10 lines, with code
  where helpful).
- Do not create an issue just to fill the daily quota. Fewer high-quality drafts
  are better than five weak drafts.
- Skip legal, security-sensitive, medical, financial, or personal topics.
- Do not mention my own projects unless clearly relevant.
- **Do not draft replies that mainly promote** a tool, library, blog post, or
  my profile. If the most useful answer would be "use library X" and X is
  mine, skip the draft.
- Cite official docs / standards when appropriate; never invent APIs or flags.

## Discussion handling

- **Always read existing comments and the chosen answer (if any) before
  drafting.**
- **Skip discussions that already appear solved**, even if not marked
  `isAnswered`. Cues: OP saying "thanks, fixed", a working solution in a
  comment with no follow-up problems, an explicit "resolved" remark, lots of
  positive reactions on a single comment.
- **Do not repeat an existing answer.** Draft only if you can add a clearly
  useful correction or missing detail (a caveat, a better idiom, a related
  pitfall). Lead the draft by naming what new info you're adding
  (e.g. "Adding to @user — one caveat: …").
- Before creating an issue, search existing open and closed issues in
  `gistrec/community-assistant` for the discussion URL. If the URL was already
  reported, skip it.

## Target topics

Primary:

- C++ (modern C++, std library, build / link issues)
- Python (packaging, environments, common patterns)
- CMake

Also welcome:

- vcpkg / Conan
- header-only libraries
- GitHub Actions / CI
- packaging
- geospatial algorithms

## Tooling

Use `gh api graphql` for everything related to GitHub Discussions.
**Do not use `gh search discussions` — that command does not exist.**

GraphQL is required for:

- **Listing repository discussions:**
  `repository.discussions(first, orderBy, answered)`.
- **Filtering unanswered at the API level:** pass `answered: false` to the
  connection. GitHub added this argument in October 2023. Use it first, but
  still read `isAnswered`, `answer`, and `answerChosenAt` defensively.
- **Reading content per discussion:** `title`, `bodyText`, `url`,
  `author { login }`, `createdAt`, `updatedAt`, `isAnswered`,
  `answer { bodyText author { login } createdAt }`, `answerChosenAt`,
  `comments(first: N) { totalCount nodes { bodyText author { login } createdAt isAnswer } }`,
  `category { name isAnswerable }`.

For finding candidate repositories, `gh api graphql` with
`search(type: REPOSITORY, query: "topic:<topic>")` is fine.

**Read-only on external repos.** The only allowed write call is:

```
gh issue create -R gistrec/community-assistant ...
```

Any other write call against another repo is a bug — stop and report.

## Output format

Create one GitHub issue in `gistrec/community-assistant` per draft.

When creating issues, add labels:
- `draft`
- `github-discussion`
- `needs-review`

If labels do not exist, create the issue without labels.

If no drafts pass the bar, do **not** create an issue. Print a short summary of
what was checked and end with `No drafts today.`

Issue title:

```text
Draft: <owner/repo> — <discussion title>
```

Issue body:

```markdown
- **Discussion:** <url>
- **Repository:** <owner/repo>
- **Author:** @<discussion author>
- **Updated:** <discussion updatedAt>
- **Topic:** <one of the target topics>
- **Confidence:** high | medium
- **Why useful:** <short reason this draft is worth posting>

## Context

<1–3 sentences summarising the discussion and existing comments>

## Draft reply

<answer text>

## Checklist

- [ ] I reviewed the original discussion
- [ ] I verified the answer is not duplicating an existing comment
- [ ] I posted the reply manually
```

## Limits per run

- Max 5 drafts total.
- Max 1 draft per repo.
- Skip if the draft would be longer than ~15 lines or require significant
  guesswork.
- Prefer discussions **updated within the last 90 days** — older threads are
  usually stale.
- Prefer categories where `category.isAnswerable = true` (Q&A-style).
- Skip announcement / general / show-and-tell categories — no accepted
  answer is expected there.
- Do not create drafts just to reach the limit. Zero drafts is acceptable.
