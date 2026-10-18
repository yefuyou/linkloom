# Task revision Dev fixtures

This directory materializes the public Dev slice for the M2 temporal task
revision qualification. It is fixture data only: no Provider, evaluator, or
production task revision implementation is run by this fixture step.

The public input is [`inputs/dev.json`](inputs/dev.json). It uses
`task-revisions-input/v1` and contains only synthetic source artifacts,
M1-shaped verified task observations, and revision questions with exact source
quotes. A question names the new observation as `subject` and the old
observation as `object`; `as_of` is present only when the question asks for a
valid-time projection. It contains no verdict, expected delta, Gold reason,
observation ID, source hash, or generated implementation output.

The evaluator-only Gold is [`gold/dev.json`](gold/dev.json). It uses
`task-revisions-gold/v1` and records the expected verdict, changed fields,
comparison status, observed dates and boundary semantics, calendar delta,
explanation, and exact evidence quotes. `effective_from` and
`plan_currentness` are recorded only for V05's explicit future-effective
revision. Gold is kept on a separate path and must not be placed in the model
input or retrieval directory.

The pinned public cases are:

- D01: same task with a name change and an explicit statement that the plan is
  not revised; revision is REFUTED and comparison is NOT_APPLICABLE.
- D04: the M2 design's exact A/B/C source family. B revises the deadline by
  four days; C revises scope while keeping the deadline at the same boundary,
  producing a zero-day comparison.
- D05: same task and different dates without revision evidence; comparison is
  BLOCKED.
- D07: the public five-line supplier-change copy where B has no project field
  or unique bridge back to A; revision is UNKNOWN and comparison is BLOCKED.

V01–V07 are independent boundary checks using different people, projects,
ticket keys, artifacts, and dates:

- V01 has a month/day-only old deadline, so a year cannot be filled from the
  source timestamp and the comparison is NOT_COMPARABLE.
- V02 changes the explicit boundary from BEFORE to ON_OR_BEFORE, so the
  numeric dates do not make the comparison comparable.
- V03 explicitly labels a date difference as source/recording correction and
  explicitly rejects a business plan revision. The two distinct synthetic
  source artifacts remain CURRENT; the old one is not automatically STALE.
- V04 provides two valid, contradictory replacement claims. The relation is
  CONFLICT and the comparison is BLOCKED; no last-write-wins choice is encoded.
- V05 supplies an explicit future effective date and asks `as_of` before that
  date. The revision and +5-day comparison are computable, while
  `plan_currentness` is FUTURE_EFFECTIVE.
- V06 covers a supported earlier deadline with a -6-day delta.
- V07 explicitly claims a deadline revision even though the numeric day is the
  same; the defensible result is COMPUTABLE with a zero-day delta.

Every task deadline in the input is an explicit M1 date token. Missing-year
and boundary cases retain their source precision; no year, boundary, project,
or task identity is invented from metadata or adjacency. Revision evidence
quotes cover the complete finite source statement, including identity and
negation/replacement context, rather than a date-only fragment.

This directory deliberately excludes H01–H04, holdout Gold, provider runs,
database state, and changes to the existing task-identity or task-relations
fixtures. Source validation may use the existing M1 pure observation builder;
it must not run a new production evaluator or any holdout case.
