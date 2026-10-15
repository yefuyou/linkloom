# LinkLoom V1 Design Specification

**Design owner:** Gemini, invoked through the official Antigravity CLI (`agy` 1.2.2)
**Engineering owner:** Codex
**Selected direction:** Decision Brief + Split Source Inspector
**Primary demo case:** `mps-001` — Atlas Lantern model-provider decision
**Reference viewport:** 1440 × 1024
**Status:** Design complete; ready for implementation

## 1. Product position

LinkLoom is a decision-recovery workspace, not a chatbot, document summarizer, generic knowledge-base search screen, agent-trace viewer, or developer runtime console.

The screen must answer four questions in this order:

1. What did the team finally decide?
2. Why did it decide that?
3. Why should I trust this reconstruction?
4. What must happen next, and what is still unresolved?

The product promise is **decision recovery with claim-level evidence grounding**. The visual design therefore treats the reconstructed decision and its proof as one connected reading experience.

## 2. Selected direction

V1 uses an editorial **Decision Brief** on the left and a synchronized **Source Inspector** on the right.

The brief reads like a concise internal decision memo rather than a collection of dashboard cards. Claims contain human-readable inline citations. Selecting a citation, claim, action, or unresolved item focuses the corresponding source passage in the inspector. Evidence is visible as the basis of the answer, not as an appendix of internal IDs.

This direction was selected because it:

- communicates “the final decision” immediately;
- makes evidence inspection obvious without requiring an explanation of LinkLoom;
- maps cleanly to the existing `TeamDecisionResult` and evidence ledger;
- keeps the agent trajectory secondary;
- can be implemented without inventing new backend capabilities;
- produces a clear portfolio/demo image at a single desktop viewport.

## 3. Core user flow

1. The user opens a workspace and sees a quiet query surface with project context.
2. The user asks a decision question.
3. The brief changes into a restrained running state: searching, reading, checking evidence, then preparing the answer.
4. When ready, the reconstructed decision receives visual priority.
5. The user reads the rationale and alternatives in the brief.
6. The user selects a citation or evidence-backed statement.
7. The inspector opens the exact source passage and preserves the claim-to-source relationship.
8. The user reviews actions and epistemic gaps.
9. If evidence is insufficient, LinkLoom states that as the valid answer and shows the nearest relevant evidence without manufacturing certainty.
10. If the runtime or provider fails, the product displays an operational failure distinct from an evidence-based “not enough information” result.

## 4. Desktop page structure

At widths of 1280 px and above, use a fixed two-surface reading workspace:

- 56 px product/context bar;
- approximately 60% width Decision Brief;
- approximately 40% width Source Inspector;
- a single vertical divider between the two surfaces;
- independently scrollable brief and inspector bodies;
- no permanent navigation sidebar for V1.

At 1024–1279 px, use approximately 55% / 45%. Below 1024 px, use a single-column brief and open the Source Inspector as an off-canvas sheet.

### 4.1 Product/context bar

The top bar contains only the context needed to understand the result:

- LinkLoom wordmark;
- workspace or project name;
- compact document count when the backend can provide it;
- a `How LinkLoom found this` disclosure after a run exists.

Provider names, tokens, turns, tool names, checkpoint IDs, hashes, and runtime controls do not appear in the primary chrome.

### 4.2 Decision Brief

The brief is a semantic document, not a card grid. Its order is:

1. Workspace and user question
2. Outcome state and final decision
3. Why this decision was made
4. Alternatives that were considered and rejected
5. Follow-through / actions
6. What is not yet settled
7. Collapsed provenance disclosure

The exact order is a presentation choice; it does not mirror the backend JSON field order.

### 4.3 Source Inspector

The inspector contains:

- source filename and lightweight source metadata;
- a source body when full note text is available;
- exact highlighted line or passage related to the selected claim;
- previous/next evidence navigation;
- an explanation of which brief claim the passage supports;
- a quote-only fallback when only ledger excerpts are available.

Internal evidence references may be retained in accessible metadata or technical details, but they are not the main visible label.

## 5. Wireframes

### 5.1 Initial/query state

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ LinkLoom                 Atlas Lantern · 6 project notes                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Recover a team decision                                                    │
│  Ask what the project finally decided. LinkLoom will find the records       │
│  and connect the answer to its evidence.                                    │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ Which model provider was finally approved for the Atlas Lantern pilot?│  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                          [Recover decision] │
│                                                                             │
│  Search is read-only. Your notes are not changed.                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

The initial state is quiet and focused. It does not display an empty source panel, empty dashboard cards, or fake recent activity.

### 5.2 Running state

```text
┌──────────────────────────────────┬──────────────────────────────────────────┐
│ Atlas Lantern                    │ Finding the basis for the answer         │
│                                  │                                          │
│ Which model provider was finally │ ✓ Searched the workspace                 │
│ approved for the pilot?          │ ✓ Read the final decision                │
│                                  │ ● Checking follow-through                │
│ Reconstructing the decision…     │ ○ Preparing the answer                   │
│                                  │                                          │
│ LinkLoom is comparing decision,  │ 03-final-decision.md                     │
│ rationale, and readiness notes.  │ Aster A was selected as the approved…    │
│                                  │                                          │
│                                  │ This is read-only.                        │
└──────────────────────────────────┴──────────────────────────────────────────┘
```

The running view presents product-level activity only: searched, read, checked, answer ready. It must be driven by actual run state/events when connected to a live runtime. The demo backend may replay a deterministic fixture for screenshot development, but the business UI must not invent run capabilities.

### 5.3 Successful decision result

```text
┌────────────────────────────────────────────┬────────────────────────────────┐
│ Atlas Lantern                             │ 03-final-decision.md            │
│ Which model provider was finally approved?│                                │
│                                            │ Model Provider Selection       │
│ APPROVED                                   │                                │
│ Aster A was selected as the approved       │ 10  The team selected Aster A  │
│ provider for the Atlas Lantern pilot.[1]   │ 11  as the approved provider   │
│                                            │ 12  for the Atlas Lantern pilot│
│ Why this decision                          │                                │
│ It offered the clearest auditable regional │ [Highlighted passage]          │
│ data boundary and an owned operational     │                                │
│ path, even though Borealis B had a faster  │ Supports: Final decision       │
│ prototype.[2][3]                           │                                │
│                                            │ [1] 03-final-decision.md:10–12 │
│ Alternatives considered                    │ [2] 02-comparison-review.md:…  │
│ Borealis B — rejected…[4]                  │                                │
│ Cedar C — rejected…[5]                     │                                │
│                                            │                                │
│ Follow-through                             │                                │
│ ✓ Adapter contract · Lin Qiao · Complete   │                                │
│ ! Regional validation · Chen Rui · Blocked │                                │
│ → On-call runbook · Omar Wu · In progress  │                                │
│                                            │                                │
│ What is not yet settled                    │                                │
│ Regional validation remains blocked;       │                                │
│ on-call readiness remains in progress.[6]  │                                │
└────────────────────────────────────────────┴────────────────────────────────┘
```

### 5.4 Insufficient-evidence result

```text
┌────────────────────────────────────────────┬────────────────────────────────┐
│ Atlas Lantern                             │ Nearest relevant source        │
│ When will the pilot enter production?     │ 06-adoption-readiness.md       │
│                                            │                                │
│ NOT ENOUGH EVIDENCE                        │ The provider remains approved, │
│ The workspace does not record a confirmed  │ but regional validation is     │
│ production cutover date.                   │ blocked and the on-call runbook│
│                                            │ remains in progress.           │
│ What the records do confirm                │                                │
│ Aster A remains approved, while two        │ This source explains why a     │
│ readiness items are incomplete.[1]         │ date cannot yet be confirmed.  │
│                                            │                                │
│ Still unresolved                           │                                │
│ • Regional validation                      │                                │
│ • On-call runbook                          │                                │
└────────────────────────────────────────────┴────────────────────────────────┘
```

Insufficient evidence is a useful epistemic outcome, not an error card. It shows what is known, what is absent, and the closest supporting source.

## 6. Component hierarchy

```text
AppShell
├── ContextBar
│   ├── ProductMark
│   ├── WorkspaceContext
│   └── ProvenanceDisclosureTrigger
├── InitialQueryView | RunningView | ResultWorkspace | RuntimeFailureView
│   └── ResultWorkspace
│       ├── DecisionBrief
│       │   ├── QueryContext
│       │   ├── OutcomeHeader
│       │   ├── DecisionStatement
│       │   ├── RationaleSection
│       │   ├── AlternativesSection
│       │   ├── ActionsSection
│       │   ├── EpistemicGapsSection
│       │   └── ProvenanceDisclosure
│       └── SourceInspector
│           ├── SourceHeader
│           ├── SourceViewer | QuoteOnlyFallback
│           ├── EvidenceHighlight
│           └── CitationNavigator
└── ResponsiveInspectorSheet
```

Components should be defined by behavior and semantics rather than by visual boxes. Sections are separated primarily by typography, whitespace, and rules.

## 7. Evidence interaction

### 7.1 Visible citation format

In prose, show compact numeric citations such as `[1]`. On hover/focus and in the inspector navigation, resolve them to human-readable source labels such as:

```text
[1] 03-final-decision.md · lines 10–12
```

Do not lead with `ev_p1_0001` or similar internal IDs.

### 7.2 Claim-to-evidence linking

- A claim containing evidence is keyboard-focusable as a unit.
- Selecting its citation makes the claim the active claim.
- The active claim receives a restrained warm underline or pale amber background.
- The inspector switches to the matching source and scrolls to the matching passage.
- The source passage receives the same warm evidence color, creating a visual bridge.
- The inspector labels the relationship, for example `Supports: Final decision`.
- Selecting a different citation updates both sides without changing the brief’s scroll position.
- Hover shows a short source preview but must not auto-scroll the inspector.

### 7.3 Multiple evidence sources

When one claim has more than one evidence source, the inspector exposes previous/next controls and a positional label such as `2 of 3`. The main prose remains readable; it does not expand into a row of evidence cards.

### 7.4 Evidence fallback

If the backend provides only a quoted excerpt and source location, show a quote-only evidence view. Do not synthesize the surrounding note. If neither full content nor an excerpt is present, show the source location and state that no preview is available.

### 7.5 Exact `mps-001` anchors

The success demo should use the real fixture sources and verified locations, including:

- `03-final-decision.md`, lines 10–12 for the Aster A decision;
- `03-final-decision.md`, lines 16–19 for rejected alternatives;
- `03-final-decision.md`, lines 23–24 for the final rationale;
- `02-comparison-review.md`, lines 16–17 and line 22 for comparison evidence;
- `04-action-status.md`, line 15, lines 21–22, and lines 26–27 for action status;
- `06-adoption-readiness.md`, lines 15–17 for incomplete readiness.

Use actual excerpts read from those files. Do not add facts that are not present in the fixture.

## 8. Uncertainty and state language

LinkLoom distinguishes four epistemic states:

| State | Meaning | Visual treatment |
|---|---|---|
| Confirmed | Directly supported by cited records | Normal high-contrast text with optional green outcome label |
| Inferred | A bounded interpretation assembled from available evidence | Amber marker and explicit `Inferred` language |
| Unresolved | The records explicitly leave an item open, blocked, or in progress | Amber status and placement in `What is not yet settled` |
| Unknown | The available workspace does not contain enough support | `Not enough evidence` outcome and explanation of the absence |

Do not use a single generic confidence score. Uncertainty belongs beside the affected claim or outcome, not in a detached card.

Operational states are separate:

- provider unavailable;
- runtime failure;
- structured-output/schema failure;
- evidence-contract failure.

These use the runtime failure view and must never be presented as “unknown” or “not enough evidence.”

## 9. Interaction specification

### 9.1 Query

- Enter submits when the query is a single line; Shift+Enter adds a newline if multiline input is enabled.
- The submit button is disabled for empty input and while the same request is starting.
- The submitted query remains visible throughout the run and result.
- A retry reuses the current workspace and query but creates a new run.

### 9.2 Running

- Status changes are announced through a polite `aria-live` region.
- The UI polls the local run endpoint at a restrained interval.
- Running stages derive from runtime status and durable events, then map to product language.
- No decorative fake progress percentage is shown.
- If a run pauses or requires attention, the UI states that directly.

### 9.3 Evidence navigation

- Click or Enter/Space activates a claim citation.
- `j` / `k` or arrow controls move through evidence when focus is in the inspector.
- `/` returns focus to the query field when safe.
- Escape closes the responsive inspector sheet.
- Focus is moved to the inspector heading when the sheet opens on small screens.

### 9.4 Provenance

`How LinkLoom found this` is collapsed by default. When opened, it presents only the product-level sequence:

```text
Searched workspace → Read decision records → Checked supporting claims → Answer ready
```

Technical run details may appear inside a second collapsed disclosure for debugging, never in the default reading path.

## 10. Visual system

### 10.1 Typography

Prefer system fonts to avoid a font-loading dependency. If available, use `Newsreader`; otherwise use Georgia for the verdict. Use a neutral UI sans such as Inter/system-ui for the rest.

| Role | Size / line-height | Weight |
|---|---:|---:|
| Decision/verdict | 24 / 32 px | 600 |
| Page title | 20 / 28 px | 600 |
| Section heading | 16 / 22 px | 600 |
| Body | 14 / 22 px | 400 |
| Metadata | 12 / 18 px | 500 |
| Citation/source mono | 11 / 16 px | 500 |
| Source text | 12 / 20 px | 400 |

The decision statement should occupy roughly two to four lines at the reference viewport. Avoid giant hero typography.

### 10.2 Color

```text
Canvas                #FAFAF9
Brief surface         #FFFFFF
Inspector surface     #F5F5F4
Primary text          #1C1917
Secondary text        #57534E
Muted text            #A8A29E
Subtle border         #E7E5E4
Strong border         #292524
Evidence highlight    #FEF3C7
Approved              #15803D
Blocked / unresolved  #B45309
Runtime error         #B91C1C
```

Color reinforces meaning but is never the only signal. There are no purple/blue AI gradients, glow effects, glass surfaces, or decorative neon.

### 10.3 Spacing and density

- Base spacing unit: 4 px.
- Brief horizontal padding: 32 px desktop, 24 px compact desktop, 20 px mobile.
- Inspector horizontal padding: 24 px desktop, 20 px mobile.
- Major section separation: 32 px.
- Heading-to-body gap: 8–12 px.
- Related list item gap: 8 px.
- Maximum readable brief line length: approximately 72 characters.

The product should feel information-dense but breathable, like a mature research tool. Do not place every section in a rounded rectangle.

### 10.4 Surfaces and borders

- Use flat surfaces and 1 px dividers.
- Use square or 4 px corner radii only for controls that require a boundary.
- Avoid persistent shadows; the responsive inspector sheet may use a restrained shadow to communicate layering.
- Separate document sections with whitespace or thin rules rather than card containers.

### 10.5 Interaction states

- Hover: subtle canvas shift or underline.
- Focus: 2 px high-contrast outline with 2 px offset.
- Active evidence: pale amber highlight plus a dark left rule or underline.
- Disabled: reduced contrast while retaining readable text.
- Selected source: dark text, evidence highlight, and explicit `Selected` semantics for assistive technology.

## 11. Loading, empty, insufficient, and error states

### 11.1 Loading / running

Show a readable stage list and one currently inspected source or passage when available. Use no skeleton dashboard grid and no indeterminate decorative animation that implies fabricated precision.

### 11.2 Empty workspace

State that LinkLoom could not find project documents in the selected workspace and explain the next supported action. Do not show empty result sections.

### 11.3 Empty query

Keep submit disabled and provide an accessible field hint. Do not show an error toast for an untouched field.

### 11.4 Insufficient evidence

Treat it as a completed answer:

- lead with `Not enough evidence`;
- state the missing fact plainly;
- show what the records do confirm;
- show unresolved or blocking facts;
- show the nearest relevant source and why it is relevant.

### 11.5 Runtime/provider/contract error

Show a humane message such as `LinkLoom could not complete this reconstruction.` Offer retry when safe. Put the actual error code/message in collapsed technical details. Preserve fail-closed behavior: do not render a partial object as a valid team decision.

## 12. Responsive behavior

### 12.1 1024–1279 px

- Keep split view at roughly 55/45.
- Reduce padding but preserve the decision’s typographic hierarchy.
- Allow action rows to wrap owner/deadline metadata.

### 12.2 Below 1024 px

- The Decision Brief becomes the full-width primary page.
- Selecting evidence opens the Source Inspector as a right-side sheet on tablets and a bottom/full-height sheet on narrow phones.
- The sheet has an explicit close button and traps focus while open.
- Citation numbers and status text remain visible; do not remove evidence or uncertainty for mobile.

### 12.3 Narrow phones

- Stack action metadata below the action title.
- Keep the verdict at 22–24 px rather than shrinking it into body text.
- Keep touch targets at least 44 px.

## 13. Accessibility and content rules

- Use `main` for the brief and `aside` for the source inspector.
- Give the decision outcome an actual heading.
- Use lists for alternatives, actions, evidence navigation, and unresolved items.
- All evidence controls require accessible names that include source and location.
- Running changes use `aria-live="polite"`; runtime failures use `role="alert"` only when they appear.
- Maintain WCAG AA contrast.
- Do not encode approved, blocked, inferred, or unknown status by color alone.
- Keep source paths relative to the workspace in normal UI; do not expose machine-specific absolute paths.
- Render a missing owner as `Unassigned` and a missing deadline as `Not recorded`; never invent either value.

## 14. Implementation notes

### 14.1 Backend projection

Add a thin presentation adapter between the existing runtime/result artifacts and the web UI. The adapter may reorder and label fields, but must not change the meaning of `TeamDecisionResult`.

The adapter should project:

- run ID and user query;
- workspace display context;
- runtime status and product-level stage;
- final decision and claim-level evidence references;
- rationale and its references;
- rejected alternatives and their references;
- actions, owner, deadline, status, and references;
- unresolved items and references;
- uncertainty statements and references;
- evidence source metadata, quoted passage, and full source text when safely available;
- operational failure details separately from epistemic uncertainty.

### 14.2 Runtime integration

- Keep the existing runtime as source of truth.
- Use a small local HTTP/polling boundary; no queue, broker, auth system, or provider redesign is needed for V1.
- Convert durable run events into the four product-facing stages.
- Keep provider configuration outside the presentation layer.
- Keep all workspace access read-only.

### 14.3 Demo fixtures

`mps-001` may be packaged as a deterministic UI-development fixture. Fixture selection belongs to demo/preview code, not result rendering logic. The same renderer must accept any valid projected `TeamDecisionResult`.

The insufficient-evidence screenshot may ask a question not answered by the fixture, but the response must only use actual fixture facts and excerpts. It must be labeled as a demo case and must not imply a backend capability beyond the result contract.

### 14.4 Failure behavior

- Invalid or incomplete structured results fail closed.
- Missing evidence previews degrade to source metadata, not fabricated prose.
- A missing note on disk does not erase the claim; it disables full-source preview and explains why.
- Polling/network errors preserve the last trustworthy state and offer retry.

## 15. Reference screenshot composition

The primary portfolio/demo screenshot should show the `mps-001` successful result at 1440 × 1024:

- the Aster A decision is the dominant text in the upper-left;
- a selected inline citation is visibly connected to a highlighted passage in `03-final-decision.md` on the right;
- at least one blocked or in-progress readiness action is visible below the rationale;
- `What is not yet settled` is visible without scrolling or begins at the fold;
- the provenance disclosure is collapsed;
- there is no agent trace, provider selector, token meter, dashboard grid, or AI decorative chrome.

## 16. Design acceptance checklist

- A new viewer can identify the product as decision recovery with evidence in 30 seconds.
- The final decision has the strongest visual priority.
- Rationale appears before operational follow-through.
- Citations use human-readable sources rather than internal IDs.
- Selecting a citation synchronizes the brief and source inspector.
- Evidence preview has a truthful quote-only fallback.
- Confirmed, inferred, unresolved, and unknown are visually and verbally distinct.
- Insufficient evidence is distinct from runtime/provider/contract failure.
- Readiness does not imply adoption completion.
- The agent process is collapsed and expressed in product language.
- Initial, running, successful, insufficient, empty, and failure states are complete.
- Desktop split view and sub-1024 responsive inspector behavior are implemented.
- Keyboard evidence navigation and visible focus states work.
- The UI contains zero synthetic owners, deadlines, source lines, evidence counts, or business facts.
