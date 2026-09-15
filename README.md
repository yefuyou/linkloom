# LinkLoom

[English](README.md) · [简体中文](README.zh-CN.md)

**Recover what the team actually decided—and show why the answer is
trustworthy.**

LinkLoom is a local-first decision-recovery workspace for project leads, PMs,
PMOs, and team members. It searches scattered project notes, meeting records,
and decision logs, then returns a strict evidence-grounded brief: the final
decision, rationale, rejected alternatives, actions, unresolved items, and
explicit uncertainty.

It is not a chatbot, a document summarizer, or an Agent trace console. The
product question is simpler: **“So what did we finally decide?”**

![LinkLoom decision brief with linked source evidence](output/playwright/success.png)

## What the product returns

- **Decision first.** The final recorded choice leads the page instead of a
  conversation transcript.
- **Evidence at claim level.** Citations connect material claims to verified
  source excerpts and exact line ranges.
- **Reasons and alternatives.** Supporting rationale is separated from options
  that were actually rejected.
- **Actionable follow-through.** Actions, owners, due dates, and unresolved
  questions retain missing values instead of filling them in.
- **Honest uncertainty.** Confirmed results, insufficient evidence, and
  operational failure are visibly different states.
- **Provenance on demand.** Search/read activity is available as secondary
  explainability, not the product's main interface.

## Try the deterministic product demo

The local preview uses frozen synthetic project records, requires no provider
key, and never writes to the source workspace.

```bash
python -m pip install -e .
python -m linkloom.ui --port 8765
```

Open `http://127.0.0.1:8765`. The bundled `mps-001` fixture replays two
documented questions: one successful Atlas Lantern decision and one
insufficient-evidence case. Arbitrary questions fail explicitly rather than
receiving a hard-coded answer. The same interface supports English and
Simplified Chinese; use the `English / 中文` control or open `?lang=zh`.

Additional captured states:

| Initial question | Searching and reading | Insufficient evidence |
|---|---|---|
| ![Initial query](output/playwright/initial.png) | ![Running state](output/playwright/running.png) | ![Insufficient evidence](output/playwright/insufficient.png) |

## How LinkLoom works

```text
decision question
       │
       ▼
durable Agent loop ──► search_notes / read_verified_note
       │                         │
       │                         ▼
       │                 verified evidence
       ▼
strict TeamDecisionResult
       │
       ├── contract validation
       ├── claim ↔ observed-evidence validation
       └── provider-neutral UI projection
                          │
                          ▼
            Decision Brief + Source Inspector
```

The runtime supports real multi-turn Gemini and DeepSeek provider adapters.
Each durable model turn owns one structured action—one tool call or Final—and
the read-only `ToolRuntime` validates calls, budgets, results, checkpoints, and
evidence before the next turn. A host application can inject a configured
`RuntimeEngine` through `RuntimeRunBackend`; the module launcher above remains
an isolated deterministic demo.

## Real-provider evidence, reported honestly

Three frozen synthetic cases have first-result DeepSeek evidence. This is a
small engineering evaluation, not a benchmark or production success rate.

| Case | Intended product behavior | Infrastructure | Business result |
|---|---|---:|---|
| `mps-001` | Recover the approved model provider | PASS | Aster A recovered and grounded; rejected-alternative classification was too broad |
| `aer-002` | Recover a cross-document rollout boundary | FAIL | Not evaluated: the first response returned multiple tool calls |
| `iti-005` | Refuse to invent missing owners/deadlines | FAIL | Not evaluated: the first response returned multiple tool calls |

The two blocked cases were sealed as their first terminal results and were not
resampled. Their common failure is a Provider–Runtime capability mismatch:
DeepSeek may return more than one tool call for `tool_choice: auto` under its
[documented Chat Completions contract](https://api-docs.deepseek.com/api/create-chat-completion/),
while the current durable runtime accepts exactly one action per turn. Contract,
grounding, decision, scope, and uncertainty are therefore **not evaluated** for
those runs—not falsely counted as semantic failures.

See the [full evidence matrix](docs/requirements/product_evidence_portfolio_v1/EVALUATION_MATRIX.md)
for per-layer outcomes, tokens, costs, classifications, artifact locations,
and product implications.

## Safety and trust boundaries

- The current product path is read-only; retrieval tools cannot rewrite,
  rename, move, tag, or delete notes.
- Tests and demos use checked-in synthetic workspaces before any private vault.
- A material output claim may cite only evidence observed in successful tool
  results; missing or changed evidence fails closed.
- Owners and deadlines remain `null` when records do not provide them.
- Provider errors are projected through stable safe codes; raw local paths and
  untrusted error messages are not exposed in the UI.
- Any future real-vault mutation requires a visible per-file dry run, exact
  approval, source-version validation, backup, audit, and rollback.

## Project status and limitations

The decision-recovery runtime, strict result contract, evidence projection,
real Gemini/DeepSeek multi-turn baseline, and Product UI V1 are implemented and
locally tested. This is not yet a deployed multi-user application.

Known limitations:

- the CLI UI launcher is a deterministic local demo; live product hosting must
  inject a configured runtime;
- the demo contains one frozen workspace and two documented questions;
- the DeepSeek adapter currently fails closed on multiple tool calls;
- `TeamDecisionResult` does not yet expose a claim-level `inferred` flag;
- long-note pagination, auth, workspace management, deployment, and real-vault
  writes are outside this phase.

## Verification and project map

The Product UI milestone records 48 passing focused/adjacent tests, browser
checks across initial/running/success/insufficient/error states, responsive
evidence-dialog checks, a real synthetic-vault runtime projection, and Gemini
visual review through Antigravity. The frozen Team Decision seed validates 30
cases, 6 workspaces, and 36 notes.

Useful starting points:

- [Product roadmap](docs/PRODUCT_ROADMAP.md)
- [Product UI design and delivery](docs/requirements/product_ui_v1/DELIVERY.md)
- [Product UI design spec](docs/requirements/product_ui_v1/DESIGN_SPEC.md)
- [Team Decision eval seed](docs/requirements/m1_team_decision_eval_seed/README.md)
- [Real-provider evidence matrix](docs/requirements/product_evidence_portfolio_v1/EVALUATION_MATRIX.md)
- [Portfolio notes and architecture content](docs/requirements/product_evidence_portfolio_v1/PORTFOLIO_NOTES.md)
- [Integration status](docs/INTEGRATION_STATUS.md)

## License

LinkLoom is available under the [MIT License](LICENSE).
