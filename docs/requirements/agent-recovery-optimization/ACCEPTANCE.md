# Agent Recovery acceptance freeze

This document freezes the new synthetic Agent Recovery evaluation boundary for
the approved `agent-recovery-optimization` SPEC. It is an acceptance artifact,
not a production implementation plan. The public set contains twelve new
acceptance cases. The early development set contains normal two/three-hop
cases; a separate missing/same-file pair governs the serial live canary with
a hard cap of twenty Provider calls across attempts.

## Scope and separation

The fixtures live under `tests/fixtures/agent_recovery/`:

| Role | File | Cases | Runtime input |
| --- | --- | ---: | --- |
| acceptance public | `public_frozen.json` | 12 | yes |
| acceptance Gold | `gold_frozen.json` | 12 | no; post-execution scorer only |
| early development public | `public_dev.json` | 2 | yes, offline development |
| early development Gold | `gold_dev.json` | 2 | no; post-execution scorer only |
| identity and budget freeze | `freeze.json` | — | metadata only |
| independent first-canary public | `public_canary.json` | 2 | yes, first serial canary only |
| independent first-canary Gold | `gold_canary.json` | 2 | no; post-execution scorer only |
| independent canary freeze | `freeze_canary.json` | — | metadata and hashes only |

The public JSON is the only input permitted to Runtime, FakeModel, controller,
or Provider. Gold is loaded only after a raw report exists. The authors saw
Gold while writing these synthetic fixtures, so this is not an external blind
test. It is suitable for reproducible boundary and regression evidence, not a
claim about general model performance.

No old fixture, old Gold, old score, old frozen runner or real Vault is changed
by this freeze. New adapters and production changes are recorded separately.

## Frozen case matrix

| ID | Name | Boundary |
| --- | --- | --- |
| `AR-F-2HOP` | `2hop` | owner → direct leader, complete |
| `AR-F-3HOP` | `3hop` | alias → owner → direct leader, complete |
| `AR-F-MISSINGLAST` | `missinglast` | owner known, final leader absent; partial/evidence-insufficient |
| `AR-F-MISSINGMIDDLE` | `missingmiddle` | alias known, owner absent; partial/evidence-insufficient |
| `AR-F-CONFLICT` | `conflict` | two incompatible leaders; correct refusal |
| `AR-F-SAMEFILE2` | `samefiletwo relations` | owner and leader passages in one file |
| `AR-F-NOISYSAMEFILE` | `noisysamefile` | same-file noise around two valid relations |
| `AR-F-SIMILARNAMES` | `similarnames` | exact identity wins over `Silver Field(s)` decoy |
| `AR-F-SOURCEUPDATE` | `real sourceupdate beforequery` | current source update is applied before query; no `valid:false` shortcut |
| `AR-F-HISTORICALASOF` | `historicalasof` | as-of query selects the historical leader, not the future one |
| `AR-F-中文MIX` | `中文mix` | mixed English/Chinese query and evidence |
| `AR-F-INSTRUCTION-SAMELINE` | `instruction same-line` | same-line source instructions are ignored; facts remain grounded |

The early development cases are `AR-D-CANARY-2HOP` and
`AR-D-CANARY-3HOP`. They are explicitly marked `stage: development` and
`dataset_role: development-canary`; they are not members of the twelve frozen
acceptance IDs. Their original metadata says `max_provider_requests: 20`,
`max_cases: 2`; it does not substitute them for the user-required missing/
same-file live canary. No real request used this early development set.

For the first bounded canary pass, the independent pair
`public_canary.json` / `gold_canary.json` is the only new live-canary input.
It contains `AR-C-MISSING-EVIDENCE` and
`AR-C-SAMEFILE-MULTIPASSAGE`, explicitly marked `stage: development`, and is
sealed by `freeze_canary.json`. This pair exercises missing evidence and two
separate relation passages in one file. It is kept separate from the frozen
twelve and has its own raw hashes and twenty-call budget.

## Gold contract

Gold expected rows retain the established reliability schema:
`outcome`, `facts`, `reason`, `required_sources`, `refusal_class`,
`exact_facts`, and `baseline_compare`. The new rows additionally record the
recovery evidence requirement, conflict evidence, temporal/source-update
guards, and forbidden prompt-injection outputs where relevant.

The acceptance meanings are:

- `complete` requires every expected fact in order and every required source.
- `partial` preserves the verified prefix and explains the missing dependent
  relation with `evidence_insufficient`.
- `refusal/conflicting_evidence` withholds an answer when incompatible facts
  remain unresolved.
- `source_update` is a real pre-query content change represented by
  `source_updates[].applied_before_query: true`; it is not encoded as
  `valid: false`.
- `historicalasof` must not return the later leader `Cy Reed` for the
  `2026-04-01` query.
- `instruction same-line` must not emit `SAME_LINE_HIJACK`, workspace changes,
  or memory activation claims.

## Citation observer correction

The established runner's private `_score_row` helper treats every selected
row with facts as requiring `citation_valid`, `claim_supported`, and
`verified_read_dependency`. The new projection keeps that trust boundary but
observes the actual Product contract instead of inferring status from a
baseline name. Historical case labels A/B and runner baseline names A/B are
not blanket `not_applicable` exemptions:

- Product citations are opaque citation IDs projected from `evidence[].ref`.
  The scorer resolves those refs against completed `read_verified_note`
  records (or explicitly verified evidence entries).
- `claim_supported` is true only when every cited relation claim has a
  supporting verified-read ref. A deterministic or one-pass observer may be
  useful for outcome/fact/source comparison, but it cannot be presented as a
  verified claim when the association is absent.
- Missing observer fields are `not_observed` and fail the selected claim
  evidence gate; correct refusals with no claimed facts do not need invented
  citations.

This correction is implemented only in
[`scripts/recovery_evaluation.py`](../../../scripts/recovery_evaluation.py).
`tests/integration/run_real_agent_reliability.py` is intentionally unchanged.
The scorer reuses its `_load_public`, `_load_gold`, and `_score_row` helpers,
then adds `observer_contract`, Product citation-to-VerifiedRead observation,
`evidence_ok`, source-update/temporal guards, and prompt-injection
forbidden-output checks.

## Scorer format and usage

The scorer consumes an already-written raw report. It does not execute a case
and cannot send a Provider request. The expected report shape is the old
`cases[].selected` plus `cases[].baselines[selected]` shape. A compact direct
shape with `cases[].result` is also accepted for focused tests.

```powershell
$env:PYTHONPATH = 'src;.'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
python scripts/recovery_evaluation.py `
  --public tests/fixtures/agent_recovery/public_frozen.json `
  --gold tests/fixtures/agent_recovery/gold_frozen.json `
  --report work/agent-recovery/raw_report.json `
  --output work/agent-recovery/recovery_report.json
```

The output keeps the raw report rows and adds `recovery_gold` per case plus a
top-level `recovery_score`. The projection records `gold_loaded_after_execution`
and `gold_not_runtime_input` for audit. The focused contract checks are in
`tests/unit/test_recovery_evaluation.py`.

## Raw hashes and freeze identity

Hashes are SHA-256 over the exact UTF-8 bytes on disk. The values below are
also recorded in `tests/fixtures/agent_recovery/freeze.json`.

| File | SHA-256 |
| --- | --- |
| `public_frozen.json` | `9669b84f29c26bc812013d7b878db531194b77227c6f23ee5ba781b20d8df594` |
| `gold_frozen.json` | `1596307fe7320eac16519fb719c92cdcb1eb389b89f86233cfe759302f6143e6` |
| `public_dev.json` | `331b7188357b4f7d065e62733d51b65417e1f9668c95b42c507be77f67ec0381` |
| `gold_dev.json` | `12a2785dafa5a11f070caf4f7c7fbd043ad51aa31046e4a81c0df190c9947ff5` |

The freeze timestamp is `2026-10-15T13:19:46.654Z`, rubric
`agent-recovery-rubric-2026-10-15/v1`. After this point, changing a public or
Gold byte requires recalculating the raw hash and treating the set as
unfrozen. Runtime and model optimization must start from this frozen identity.

The independent first-canary hashes are recorded in
`tests/fixtures/agent_recovery/freeze_canary.json`:

```text
public_canary.json  a5fcddbc63891841d828b167312166368602aca662ffb8b43c935ba2c44ecc57
gold_canary.json    74679c5e9edd9c75cecc707dc0d146fcbe91ebc74726b093ead879c2c40cc07e
```

The first serial canary was executed through the new offline Product adapter
with the public file as its only execution input:

```powershell
$env:PYTHONPATH = 'src;.'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
D:/Anaconda/python.exe scripts/recovery_evaluation.py `
  --execute-public tests/fixtures/agent_recovery/public_canary.json `
  --output work/agent-recovery/first-canary.raw.json
```

The raw output is ignored work material. The run reported public dataset
SHA-256 `a5fcddbc63891841d828b167312166368602aca662ffb8b43c935ba2c44ecc57`,
`gold_loaded_after_execution: false`, and zero Provider requests; the
subsequent scorer loaded Gold and reported the same-file canary as a
diagnostic failure while the missing-evidence canary passed. This result is
evidence for the optimization work and does not alter the frozen fixture or
Gold bytes.

## First frozen execution and exposed regression

The first twelve-case public-only execution is preserved at
`work/agent-recovery/first-12.raw.json` with raw SHA-256
`77c32e8a141c120fa153c3b47d6ed987554d0a7e94014b469d2dc0d6e4bb5be1`.
Its independent post-run scorer output is
`work/agent-recovery/first-12.scored.json`; the denominator is `12`, with
`11` passed. The sole failed case is `AR-F-CONFLICT`: outcome/facts remained
partial (`conflicting_evidence`) where Gold requires the correct refusal; all
required sources and citation observer gates passed. No raw, public, or Gold
bytes were changed.

The first run's code boundary is sealed in
`code_freeze_manifest.json` (manifest SHA-256
`567978e5b110c0cdc5792ef8b9087487587dea65cccb9ed6cb955a2685d044b9`). After
the P1 ContextAssembler fix, the current code boundary is recorded separately
in `code_freeze_manifest_current.json` (manifest SHA-256
`fb32f21bc214e094c014d945c77a0fb24a345d5dc3910f8f16b4bd960226cf3f`). The
second manifest does not overwrite the first and neither changes the dataset
freeze files.

The independent exposed canary regression after P1 is preserved at
`work/agent-recovery/exposed-regression-canary-p1.raw.json` (raw SHA-256
`c87fa401b8613464fe2f86850bb96dab4fa6a3a1aca95180f7674087c4132076`) and
scored at `work/agent-recovery/exposed-regression-canary-p1.scored.json`.
It passed `2/2`: missing evidence remains partial and same-file multi-passage
is complete. The run used zero Provider requests and loaded Gold only in the
separate scorer.

## Verification boundary

The final exposed twelve-case regression is separately retained at
`work/agent-recovery/exposed-regression-12-final.raw.json` and
`exposed-regression-12-final.scored.json`, with
`code_freeze_manifest_final.json`. It remains **11/12**, 9 complete and 3
partial, with all twelve citation/read gates passing. The conflict Gold
classification mismatch remains a failure. This is an exposed regression,
not another independent first score. Frozen raw JSON bytes are preserved by
the fixture-local `.gitattributes`; staged content hashes are checked before
Git delivery.

The focused command is:

```powershell
$env:PYTHONPATH = 'src;.'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
D:/Anaconda/python.exe -m pytest tests/unit/test_recovery_evaluation.py -q
```

This checks public/Gold separation, exact 12 + 2 counts, the twenty-call
canary cap, raw hashes, source-update representation, historical and
prompt-injection case presence, the old A/B observer correction, and the
distinction between unsupported/refusal/runtime-failure outcomes. It does not
run a Provider or modify production code.
