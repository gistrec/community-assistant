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
- **Never write a bare `@<login>`** anywhere in the issue body — GitHub will
  turn it into a mention and notify that user, which we don't want for
  unposted drafts. Prefer descriptive references ("OP", "the answer above",
  "the previous comment"). If a specific login is genuinely necessary, write
  it as a markdown link — `[@<login>](https://github.com/<login>)` — never
  a bare `@<login>`.

## Discussion handling

- **Always read existing comments and the chosen answer (if any) before
  drafting.**
- **Skip discussions that already appear solved**, even if not marked
  `isAnswered`. Cues: OP saying "thanks, fixed", a working solution in a
  comment with no follow-up problems, an explicit "resolved" remark, lots of
  positive reactions on a single comment, a comment with `isAnswer = true`.
  When in doubt, skip — duplicating a working answer is worse than missing a
  draft.
- **Do not repeat an existing answer.** Draft only if you can add a clearly
  useful correction or missing detail (a caveat, a better idiom, a related
  pitfall). Lead the draft by naming what new info you're adding (e.g.
  "Adding to the answer above — one caveat: …"). Refer to prior commenters
  descriptively; if a login is unavoidable, use the link form from Rules
  (`[@<login>](https://github.com/<login>)`) — never bare `@<login>`.
- Before creating an issue, search existing open and closed issues in
  `gistrec/community-assistant` for the discussion URL. If the URL was already
  reported, skip it.

## Target topics

Primary:

- C++ (modern C++, std library, build / link issues)
- Python (packaging, environments, common patterns)
- CMake

Also welcome (treat as full search seeds, not just post-hoc filters):

- vcpkg / Conan
- header-only libraries
- GitHub Actions / CI
- packaging
- geospatial algorithms

Every topic above is eligible as a query keyword in the discussion search
described under "Tooling". Don't restrict the candidate pool to only the
primary three.

## Seed repositories

The routine should always scan unanswered discussions in this curated list
before / alongside any search-based discovery. These are high-traffic projects
where good Q&A regularly appears and the search-by-topic path tends to miss
them (no `topic:` tag, or buried by larger repos).

- `microsoft/vcpkg`
- `microsoft/STL`
- `pybind/pybind11`
- `nlohmann/json`
- `fmtlib/fmt`
- `gabime/spdlog`
- `catchorg/Catch2`
- `google/googletest`
- `conan-io/conan`
- `python-poetry/poetry`
- `pypa/setuptools`
- `astral-sh/uv`
- `actions/runner`

Skip any seed repo where `hasDiscussionsEnabled = false` (verify defensively —
this list can drift). It is fine to add/remove entries; keep it small (≤ ~20)
and only include repos with active answerable categories.

## Pre-publish review

Every draft must pass an automated review through OpenAI Chat Completions
(`$OPENAI_API_KEY`, default model `gpt-5`, override via
`OPENAI_REVIEW_MODEL`) before it becomes an issue. The review is a **gate**,
not advisory:

- `reject` → the draft is dropped and **no issue is created**.
- `revise` → an issue is created, but the body gets a `## ChatGPT review`
  section listing the model's rationale and each flagged issue. The
  maintainer revises before posting.
- `approve` → the issue is created as-is.

If the OpenAI call fails (missing key, network, non-200, malformed JSON),
the routine **fails closed**: the draft is dropped and the reason is
recorded in the run report. Do not retry. See
`.claude/scripts/review_draft.py` for the integration and exact JSON
contracts.

## Tooling

Use `gh api graphql` for everything related to GitHub Discussions.
**Do not use the CLI `gh search discussions` — that subcommand does not
exist.** GraphQL `search(type: DISCUSSION, ...)` does exist and is the
preferred way to find candidate threads — they are not the same thing.

GraphQL is required for:

- **Global discussion search (preferred discovery path):**
  `search(type: DISCUSSION, query: "<keywords> is:unanswered is:open created:>=<date>", first: N)`.
  This finds threads across all of GitHub without needing to first enumerate
  repositories. Query qualifiers worth combining: `is:unanswered`, `is:open`,
  `created:>=YYYY-MM-DD`, `updated:>=YYYY-MM-DD`, `in:title,body`,
  `language:<lang>`, `repo:<owner/name>`. Treat `is:unanswered` and `is:open`
  as best-effort and still verify `isAnswered`, `answer`, `answerChosenAt`,
  `closed` per node.
- **Listing repository discussions** (for the seed list and any repo-scoped
  scan): `repository.discussions(first, orderBy, answered)`. Pass
  `answered: false` (GitHub added this in October 2023) and prefer
  `orderBy: {field: CREATED_AT, direction: DESC}` so the freshest questions
  surface first. Still read `isAnswered`, `answer`, `answerChosenAt`, and
  `closed` defensively.
- **Reading content per discussion:** `title`, `bodyText`, `url`,
  `author { login }`, `createdAt`, `updatedAt`, `closed`, `isAnswered`,
  `answer { bodyText author { login } createdAt }`, `answerChosenAt`,
  `comments(first: N) { totalCount nodes { bodyText author { login } createdAt isAnswer } }`,
  `category { name isAnswerable }`.

For repository-level discovery (as a third, fallback path), `gh api graphql`
with `search(type: REPOSITORY, query: "topic:<topic> language:<lang> pushed:>=<date>")`
is fine. Combine `topic:` **and** `language:` — many active C++/Python repos
don't set a `topic:` tag, so `topic:` alone is too narrow. Paginate past the
first 25 results when the query is broad.

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
## Discussion

- **Discussion:** [<discussion title>](<url>)
- **Repository:** [<owner/repo>](https://github.com/<owner/repo>)
- **Author:** [<discussion author>](https://github.com/<discussion author>)
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

If the pre-publish ChatGPT review returned `verdict: revise`, insert a
`## ChatGPT review` section between `## Draft reply` and `## Checklist`
with the model's rationale and the flagged issues. Omit the section when
the verdict is `approve`. Drafts with verdict `reject` are not turned into
issues at all.

## Limits per run

- Max 5 drafts total.
- Max 1 draft per repo.
- Skip if the draft would be longer than ~15 lines or require significant
  guesswork.
- Only consider discussions **created within the last 30 days** — older
  threads are usually stale or already worked through in comments.
- Skip **closed** discussions (`closed = true`) — the author isn't seeking
  more input.
- Skip discussions that already have an adequate answer in any comment, even
  if not marked as the chosen answer. Only draft if you can add a clearly
  useful correction or missing detail.
- Prefer categories where `category.isAnswerable = true` (Q&A-style).
- Skip announcement / general / show-and-tell categories — no accepted
  answer is expected there.
- Do not create drafts just to reach the limit. Zero drafts is acceptable.
