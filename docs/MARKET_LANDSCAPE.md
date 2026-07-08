# Market Landscape

Vault Steward should not compete as another generic chat-with-docs app. It
should learn from adjacent tools, then focus on the narrower job of keeping a
Markdown knowledge base understandable, auditable, and safe to reorganize.

## Adjacent Tools

### Obsidian And Local PKM

- Smart Connections: https://github.com/brianpetro/obsidian-smart-connections
- Smart Plugins: https://smartconnections.app/
- Reor: https://github.com/reorproject/reor

What to learn:

- semantic note discovery;
- related-note surfacing while writing;
- local-first retrieval;
- graph or cluster views that reveal meaning-level neighborhoods;
- "ask your notes" RAG workflows.

What not to copy blindly:

- treating chat as the whole product;
- hiding organization changes inside opaque AI magic;
- coupling the product to one user's personal folder structure.

### Larger RAG And Agent Platforms

- Onyx: https://github.com/onyx-dot-app/onyx
- AnythingLLM: https://github.com/Mintplex-Labs/anything-llm

What to learn:

- document ingestion pipelines;
- hybrid retrieval and citation-backed answers;
- connector boundaries;
- agent actions and permissioning;
- scheduled tasks, memories, and workspace-level workflows.

What not to copy yet:

- enterprise deployment complexity;
- multi-user administration;
- broad connector catalogs before the local Markdown workflow is trustworthy.

## Vault Steward Differentiation

Vault Steward should focus on:

- evidence-backed structure diagnosis;
- duplicate, empty-page, broken-link, and suspicious migration-loss detection;
- proposed taxonomy and link improvements;
- dry-run move, merge, and rewrite plans;
- exact human approval before mutation;
- learning and project continuity across days.

The product question for every feature:

```text
Does this help the user understand, trust, and safely improve their knowledge
base structure?
```

If the answer is only "it can chat with documents," the feature is probably too
generic.
