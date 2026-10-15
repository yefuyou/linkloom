# LinkLoom Product UI V1 — Gemini Design Directions

Author: Gemini 3.8 Flash High via Antigravity CLI 1.2.2
Date: 2026-09-15
Status: Design exploration complete

## Design framing

LinkLoom exists to repair decision amnesia and evidentiary erosion. When a
project lead asks what was finally decided, they need an unambiguous verdict,
the evidence chain behind it, discarded paths, and the operational frontier.
They do not need a conversational assistant, a developer console, or a grid of
generic dashboard cards.

The interface must distinguish six semantic states structurally, not through a
row of decorative badges:

1. **Confirmed / approved** — high-contrast type anchored to an authoritative
   source.
2. **Inferred / partial** — qualified language and a visibly weaker boundary.
3. **Unresolved / in progress** — an explicit open operational gate with owner
   and date when supported.
4. **Unknown / insufficient evidence** — an explicit record-silence treatment,
   never an invented placeholder value.
5. **Conflicting evidence** — opposing sources shown with the applicable
   authority rule.
6. **Runtime / validation error** — a separate integrity failure that is not
   disguised as business uncertainty.

## Direction 1 — Decision Brief and Split Source Inspector

### Core interaction model

An asymmetric 60/40 master-detail workspace. The left side is an editorial
decision brief; the right side is a persistent, read-only source inspector.
Selecting any claim, rationale, rejected alternative, action, or unresolved
item focuses the exact supporting quote and location on the right.

### Page hierarchy

1. Project/workspace context and current question.
2. Authoritative decision, status, and ratification context.
3. Grounded rationale.
4. Rejected alternatives and documented reasons.
5. Actions and readiness gates.
6. Record gaps and uncertainty.
7. Source inspector showing filename, document authority, line location, and
   the verified quote in nearby context.

### First impression

The eye lands on the decision: “Aster A — approved provider for the synthetic
pilot.” At the same time, the source inspector opens the approved decision
record and highlights the passage that supports the claim. The relationship
between answer and proof is immediately visible.

### Decision and evidence relationship

Claims use quiet inline citation markers and a focus treatment. Selecting a
claim strengthens its rule or marker and focuses the matching source passage.
No opaque evidence IDs are presented as the main user language.

### Uncertainty

Open readiness gates and missing evidence appear in a dedicated frontier/gaps
section. A blocked legal validation remains visibly different from an unknown
owner or a conflicting source. Informational or superseded notes are marked as
non-authoritative when inspected.

### Agent provenance

Hidden by default behind “How LinkLoom found this.” If opened, it describes
product-level activity such as searching notes, checking an authoritative
record, reconciling a status update, and validating citations. Tokens, turns,
runtime class names, and raw ledgers stay out of the normal experience.

### Why it fits

The design gives decision recovery and evidence grounding equal importance,
maps directly to `TeamDecisionResult` plus evidence records, and makes the core
value legible without explanation.

### Risks

The side-by-side mode needs roughly 1100px of width. Below that, the inspector
must become a drawer or stacked source view.

### Implementation complexity

Low to moderate. Standard CSS Grid and one shared `activeEvidenceId`-style
focus state can implement the core behavior.

## Direction 2 — Grounded Executive Memo with Marginalia

### Core interaction model

A single, publication-like memo with a 740px reading measure and a 280px
evidence margin. Each claim aligns horizontally with a source note in the
margin, like a scholarly or legal document with marginalia.

### Page hierarchy

1. Project/workspace and query control.
2. Formal decision brief heading.
3. Binding verdict and rationale.
4. Comparative evaluation and rejections.
5. Action ledger.
6. Boundary conditions and grounding limits.
7. A bottom verification colophon.

### First impression

The product reads as an authoritative executive briefing prepared for a
project lead: a clear verdict in a calm, continuous document rather than a set
of application panels.

### Decision and evidence relationship

Inline citations align with adjacent evidence notes containing the source
filename, location, and quote. A selected margin note expands source context
without navigating away.

### Uncertainty

Unknowns and active blockers live in the memo's boundary-conditions section.
Missing fields use explicit “not specified in retrieved notes” language.

### Agent provenance

Placed in the memo colophon at the end and expandable into the retrieval path.

### Why it fits

It feels mature and executive-friendly, favors uninterrupted reading, and
avoids tool-like chrome.

### Risks

Evidence marginalia can collide when paragraphs have many citations. Mobile
layouts must convert the core interaction into inline expanders, weakening the
original concept.

### Implementation complexity

Moderate to high because marginal evidence needs collision-aware alignment or
careful sticky positioning.

## Direction 3 — Dual-Horizon Analytical Ledger

### Core interaction model

A bilateral 50/50 workspace separates the binding decision baseline from the
current operational horizon. A persistent bottom evidence dock displays proof
for the selected item from either side.

### Page hierarchy

1. A top band contrasting decision status and readiness status.
2. Left: selection, rationale, and rejected alternatives.
3. Right: critical-path readiness gates, actions, and unknowns.
4. Bottom: cross-horizon evidence dock.

### First impression

The user immediately sees the distinction between “Aster A is approved” and
“regional rollout is still gated.” This helps teams avoid confusing delivery
delays with a reversed decision.

### Decision and evidence relationship

Selecting an item on either side focuses dependencies across the divider and
loads its source excerpt into the fixed bottom dock.

### Uncertainty

Uncertainty is architectural: decision finality lives on the left while
execution risk and missing records live on the right.

### Agent provenance

Available as a secondary tab in the evidence dock.

### Why it fits

It strongly expresses the difference between a completed decision and
incomplete follow-through, which is a recurring project-management failure.

### Risks

The information density is excessive for simple questions, and two independent
columns plus a fixed dock perform poorly on small laptops and mobile screens.

### Implementation complexity

High because it requires two scroll regions, relationship focus across columns,
and a persistent reactive dock.

## Comparison

| Criterion | Split source inspector | Memo with marginalia | Dual-horizon ledger |
|---|---|---|---|
| 30-second comprehension | Immediate | High | Moderate |
| Evidence linkage | Direct, side by side | Direct but layout-sensitive | Routed through dock |
| Backend contract fit | Direct | Requires narrative stitching | Requires inferred cross-links |
| Responsive behavior | Strong | Fair | Weak |
| Implementation cost | Low–moderate | Moderate–high | High |
| Best quality | Answer and proof together | Executive reading | Decision vs rollout contrast |

## Gemini recommendation

Select **Direction 1 — Decision Brief and Split Source Inspector**.

It makes the product model understandable almost immediately: the left side
states what the team decided, and the right side shows the original document
passage proving it. It maps cleanly to the current backend, produces a strong
`mps-001` demonstration, avoids speculative features, responds well below
desktop width, and offers the best portfolio value for modest V1 complexity.
