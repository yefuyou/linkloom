# Synthetic semantic Dev (author-visible)

Eight new synthetic variants were materialized before the production proposer
was written. `inputs/dev.json` contains sources, public endpoint binding hints
for verified M1 construction and fixed query pairs; `gold/dev.json` contains
separate author labels, expected source binding and rationales. The authoring
script is not imported by the driver, proposer or fixture loader.

Coverage: paraphrased plan replacement; direct cause / explicit no impact /
missing cause in distinct messages; unresolved competing plan assertions;
source correction; distinct tasks with an explicit work link; project alias
and membership; hypothetical future impact; missing calendar year.

This is a public, author-visible Dev diagnostic, not a blind or held-out score.
M1 task endpoints and query pairs are fixed. It does not measure task extraction,
candidate discovery recall, or end-to-end Agent performance. FakeModel outputs
are invented contract probes and cannot demonstrate semantic quality.

The three real Smoke requests are predeclared as revision, causality, conflict.
Other cases are NOT_RUN for the real provider. H01–H04 remain exposed historical
diagnostics; neither old labels nor old frozen predictions are edited. Existing
M3 holdout is not used. Predictions must be written before the separate scorer
opens Gold; never pass `required_sources` or `rationale` to the tested model.
