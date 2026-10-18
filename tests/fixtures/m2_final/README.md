# M2 final retained acceptance

Fresh synthetic materialization of the original root M2_EVAL_SPEC H01–H04.
This is author-visible acceptance, not an external blind test. Source content
is copied from the original design; author proposals define fixed endpoints
and evidence, never final system verdicts. M1 validates all fields and offsets.

`inputs/holdout.json` is the only business input to the predictor.
`gold/holdout.json` is evaluator-only, including decisive proof fragments,
typed endpoint requirements, and snapshot denominators. H04 s1/s2 share current
new-version statements but have different lifecycle events. No input/source
family is shared with Dev. No preserved legacy Gold or source bytes are edited.

Reproduction protocol and implementation freeze live in
`docs/requirements/m2-integration-acceptance/`. Authoring script is never
imported by prediction. Predict refuses to open any Gold/expected path and
checks frozen hashes before evaluating. The standalone scorer runs afterward.
M3's separate holdout is not executed by these tools.
