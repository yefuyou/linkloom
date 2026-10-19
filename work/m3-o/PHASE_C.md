# Phase C checkpoint

RED `c-red.xml`: 8 reproducible false rejections, 15 already-correct cases.
Six read-only queries were blocked by topic-wide unsafe-word matches or a
compound read suffix; two task statements containing the person's name 周若
were treated as conditional merely because of the character 若.

Changes: `ui/ask_admission.py` examines unquoted command clauses and immediate
override/secret-request targets instead of rejecting every mention of sensitive
topics. Existing write verbs remain command-bound; Chinese conjunctions split
compound requests so a later real mutation remains denied. No positive semantic
classifier or new model call. `semantic_ingestion/task_discovery_veto.py` bounds
bare 若 to a word boundary, so a name-internal character cannot be a condition.
Existing actual cancellation versus discussion rules passed without changes.
`semantic_ingestion/text_scopes.py` reuses the former F quote/code range scanner
as one structural helper; exact source offsets and F masking behavior remain.

GREEN `c-green.xml`: 171 passed including M3-J, M3-N, prior admission and new
regressions. New case accounting: readonly false rejection 6/8 -> 0/8;
legitimate task false rejection 2/3 -> 0/3; eight actual mutation/override/secret
requests denied 8/8; three negative task controls remain withheld (conditional,
cancelled, quoted). Bounded 2.1k-character processing runs in a child with an
8-second timeout, with no unbounded ReDoS search.

`c-http.xml`: 94 passed including two real offline product-chain starts with
explicit task domain and original question, the unchanged M3-M 48-case matrix,
Canary offline chain, default-off adapters, frozen inputs and tool/source caps.
First `c-chain.xml` was 41 passed / 5 failures / 48 errors caused by sandbox
localhost WinError10013; `c-matrix-diagnosis.xml` isolates that environment error.
Re-run used authorized localhost access and the same external-network denial
plugin, Fake SDK and fresh output directory. No production workaround was added.
Frozen matrix keeps four Fast Paths and no misroutes/false rejection.

Independent workspace, source-version, tool whitelist and budget enforcement
were not relaxed. No Unified Ask redesign or Canary freeze edit. Remaining
limits: this bounded syntactic guard is not a complete semantic intent parser;
ambiguous imperative-looking noun phrases may still need clarification. Names
beginning with a conditional word remain ambiguous. Legacy source-relation
planner admission still has its original stricter grammar; task-domain topic
admission does not expand that separate capability. General cancellation and
quoted-report meaning remain model-quality questions, NOT_EVALUATED here.

Proceed to full affected regression and independent read-only review.
