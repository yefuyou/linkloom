# LinkLoom V1 — release notes draft

Draft for packaging review. No Git tag or GitHub Release has been created.

## What shipped

- **Sources:** import timestamped meeting artifacts as immutable versions with
  exact evidence spans; update a source through the UI and preserve its prior
  version.
- **Review:** inspect extracted decision candidates and authorize materializing
  only after human review. Supporting proposals remain non-authoritative.
- **Temporal Decision Memory:** retain current and historical decisions,
  effective dates, supersession, and source provenance.
- **Ask:** query current or as-of decisions and inspect their evidence.
- **Reconciliation:** source updates reconcile evidence identity and surface
  affected STALE records in Review Attention.
- **Semantic Ingestion reliability:** process segmented artifacts through the
  configured provider, preserve evidence bindings, and keep extraction output
  non-authoritative until review.
- **Gemini integration:** source extraction can use Gemini through an explicit
  launch-time opt-in.
- **Runtime Agent candidate capture:** a grounded result can become a separate
  reviewable AgentMemoryCandidate; capture alone does not authorize
  materialization.

## Validation evidence

The recorded V1 release acceptance was bounded to a synthetic meeting
workflow:

- the core user journey completed through the real product browser UI;
- a separate real-Gemini acceptance made six GenerateContent requests, all
  accepted on the first attempt;
- temporal before/after lookup, supersession, provenance, and proposal safety
  were verified;
- a deterministic release gate passed 408 tests;
- the installed V1 wheel passed a no-provider HTTP smoke.

The public screenshots use synthetic data and a deterministic fake provider;
they are not Gemini-generated results. These checks do not establish general
model quality, held-out performance, or production readiness.

## Known limitations

- Ask run history is not persisted as a dedicated product resource.
- There is no dedicated STALE revalidation action in the UI.
- Provider configuration remains launch-time and environment-based.
- The app is not a deployed multi-user or enterprise service.
- The recorded synthetic workflow does not establish generalization across
  arbitrary meeting formats or providers.

See the [V1.1 backlog](V1_1_BACKLOG.md) for deferred work.
