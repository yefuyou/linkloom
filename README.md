# linkloom

linkloom is a local-first, default-read-only personal knowledge assistant for
Markdown and Obsidian vaults. It is designed as a real open-source portfolio
project, not as a collection of Agent demos or a fixed folder-template generator.

It serves people who want materials, understanding, actions, and reflection to
form a long-lived loop without manually maintaining a complex knowledge system.
It must work for an existing vault first, while also supporting people who are
gradually building a new one through ongoing input and review.

The planned user journey is simple:

```text
Can Read -> Can Find -> Can Connect -> Can Organize -> Can Act
         -> Modify Only After Confirmation
```

linkloom will help users:

- search and answer questions from notes with sources;
- discover explainable relationships between notes;
- identify duplicate, isolated, weakly tagged, or disorganized notes;
- recover tasks, unresolved questions, and next actions;
- preview and confirm any rename, move, rewrite, or metadata change before it
  touches a real vault.

## Current State

Milestone 1's Read-Only Vault Scanner is implemented and independently
reviewed against a synthetic sample vault. It safely indexes existing note
structure without modifying source notes. Search, relation suggestions, health
diagnostics, action continuity, and all real-vault writes remain future work.

The next planned product slice is a read-only Vault Profile: a human-readable
portrait generated from the Scanner index. It is not implemented yet.

The confirmed product and planned Python package name is `linkloom`. The local
repository folder is still named `vault-steward`; renaming that directory is
not part of this planning change.

## Safety Defaults

- Local-first by default.
- Read-only by default.
- Synthetic sample vaults before real user vaults.
- Evidence paths for answers, relations, diagnoses, and extracted actions.
- Read tools and write tools remain separate.
- Dry-run, exact confirmation, audit evidence, and rollback before real writes.
- No silent delete, merge, rewrite, rename, or move.

## Knowledge Lifecycle

linkloom does not require one directory tree. Instead, it should support this
user lifecycle across many possible vault structures:

```text
materials enter
  -> understanding and knowledge sedimentation
  -> connections between themes, questions, and notes
  -> goals, tasks, or experiments
  -> real-world feedback and reflection
  -> reviewed knowledge returns to the vault
```

Later "self-growing" behavior means improving this visible and controllable
loop from evidence and user-confirmed rules. It never means free-form autonomous
rewriting of a user's knowledge base.

## Documentation Map

- [docs/PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md): canonical six-milestone user roadmap.
- [SPEC.md](SPEC.md): product contract and non-goals.
- [DEV_SPEC.md](DEV_SPEC.md): implementation gates mapped to the roadmap.
- [AGENTS.md](AGENTS.md): hard rules for AI contributors.
- [docs/README.md](docs/README.md): documentation navigation map.
- [docs/MULTI_AGENT_ROLES.md](docs/MULTI_AGENT_ROLES.md): Planner, Worker, Reviewer separation.
- [docs/SPEC_DRIVEN_DEVELOPMENT.md](docs/SPEC_DRIVEN_DEVELOPMENT.md): required development workflow.
- [docs/PROJECTS_REVERSE_ENGINEERING.md](docs/PROJECTS_REVERSE_ENGINEERING.md): development patterns learned from reference projects.
- [docs/MARKET_LANDSCAPE.md](docs/MARKET_LANDSCAPE.md): adjacent tools and differentiation.
- [docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md): restore-point and commit-boundary rules.
- [docs/ACCEPTANCE_CHECKLIST.md](docs/ACCEPTANCE_CHECKLIST.md): task-level acceptance checks.

No feature implementation may start until its SPEC, acceptance criteria, and
independent review gates exist and the human has approved the active planning
boundary.
