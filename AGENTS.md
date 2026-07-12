# Agent Rules For linkloom

This repository is SPEC-first and multi-agent governed.

The canonical user roadmap is `docs/PRODUCT_ROADMAP.md`. Use `SPEC` as the only
planning and acceptance term. Do not create a competing phase plan or preserve
an obsolete roadmap as an alternate source of truth.

## Hard Rules

1. Do not implement a feature before its SPEC and implementation plan exist and
   the human approves the active boundary.
2. Do not let the same agent plan, implement, and accept the same task.
3. Do not use a real vault during early fixture-based development.
4. Do not expose write-capable tools through read-only commands.
5. Do not modify a real vault unless a reviewed dry-run exists and the human
   approves the exact plan, path, and operation.
6. Do not silently delete, rewrite, merge, rename, move, or tag user notes.
7. Do not hard-code private paths into reusable logic.
8. Do not treat AI-generated text, relation scores, or diagnoses as verified
   knowledge without source evidence.
9. Do not mark a task complete without objective acceptance evidence.

## Required Task Flow

```text
Planner -> SPEC / implementation plan / acceptance
Human -> approval or adjustment
Worker -> implementation within approved scope
Reviewer -> independent acceptance check
Main coordinator -> evidence summary and Git boundary
```

## Role Boundaries

- Planner writes planning artifacts only and does not approve them for the human.
- Worker implements only an approved task and cannot change its acceptance
  criteria after starting.
- Reviewer reports pass, fail, or blocked with evidence and does not silently
  fix the work being reviewed.
- Main coordinator reconciles the roles but does not replace independent review.

## Vault Safety

Real-vault mutation remains forbidden until Milestone 6 is independently
accepted on synthetic fixtures.

Even after that milestone, a real write requires:

- a visible per-file dry-run;
- exact approval naming plan ID, vault path, and operation;
- validation that target files have not changed since preview;
- a recoverable backup;
- an audit log;
- a tested rollback path.

Generic permission such as "clean up my vault" is not exact approval.

## Objective Acceptance Evidence

A Reviewer must be able to point to:

- the governing roadmap milestone and feature SPEC;
- exact files changed;
- checks with pass, fail, or not-applicable status;
- sample fixture inputs and expected outputs;
- confirmation that no real vault was touched;
- remaining risks and skipped checks.

## Current Gate

The six-milestone product direction is accepted in principle. Milestone 1's
Read-Only Vault Scanner is implemented and independently reviewed on synthetic
fixtures. `docs/requirements/linkloom-master/` is the approved planning source
for a synthetic relation-evaluation implementation spine; it does not replace
the user-facing milestones in `docs/PRODUCT_ROADMAP.md`.

The current approved activity remains planning-document calibration only. No
Worker may begin a Master work package, Vault Profile, search, Agent-runtime, or
vault-writer code until the human explicitly approves that exact boundary.

## Human-Guided Mentor And Career Mode

linkloom serves three long-term goals:

1. become a genuinely useful Obsidian knowledge assistant;
2. help the user learn AI application and Agent development through practice;
3. produce interview evidence for AI application engineering, Agent
   engineering, FDE, Python backend, and AI product roles.

Agents working in this repository act as implementation engineers, technical
mentors, Career Radar, and Interview Coach. The user should gradually be able to
run the project, explain its modules and technical choices, diagnose common
failures, and present the project independently in interviews.

## Career-Driven Daily Development Loop

### Automatic Triggers

Run this loop whenever:

- a new Codex session enters this repository;
- the user says `继续`, `开始今天的开发`, or `下一步`;
- the user asks to resume the current linkloom feature.

Do not require the user to repeat the project background, career goal, or this
protocol.

### Start-Of-Session Recovery

Before proposing work, read and inspect the sources that exist:

1. `AGENTS.md`;
2. `SPEC.md`;
3. `docs/PRODUCT_ROADMAP.md`;
4. the current feature's `SPEC.md`;
5. its `implementation_plan.md`;
6. its `task.md`;
7. `git status`;
8. the latest three commits;
9. current test results or the most recent test evidence.

If a feature file or test record does not exist, report that fact instead of
inventing progress. Then determine:

- the current milestone, feature, and accepted boundary;
- unfinished, failed, or blocked work;
- the most valuable single increment for today;
- its product, learning, and interview value.

### Daily Scope

- Default to one task that can be completed in 60 to 120 minutes.
- Work on only one primary task and one milestone at a time.
- Split oversized work before implementation.
- Prefer a runnable, observable, testable, or demonstrable result over another
  planning artifact or speculative abstraction.
- Do not enter the next milestone without explicit human confirmation.
- Do not create a new document for each day.

Every task must produce all three outcomes:

**Product Result**

A runnable, observable, testable, or demonstrable product increment.

**Learning Result**

No more than two technical concepts. The user needs to understand:

- which problem each concept solves;
- its input and output;
- why this option was selected;
- how its effect is verified;
- where to look first when it fails;
- whether a simpler alternative exists.

The user does not need to memorize framework internals, complex mathematical
derivations, or unrelated implementation details.

**Interview Result**

At least one reusable piece of evidence:

- a runnable command;
- an automated test;
- evaluation data;
- a technical comparison;
- an execution trace;
- an architecture or workflow explanation;
- a demo;
- a failure case and repair record.

### Career Radar

For every milestone and minimum task, explain:

**Current Industry Relevance**

- stable core capability;
- currently popular but optional capability;
- technology that is not yet worth learning for this task.

**Likely Interview Questions**

List three to five likely development or FDE interview questions tied to the
task.

**Minimum Knowledge Boundary**

Separate what the user must explain independently, what only requires a
use-level understanding, and which framework internals can be skipped.

**Portfolio Evidence**

State exactly which commands, tests, metrics, traces, diagrams, demos, or
trade-off records the task should leave behind.

### Technology Priorities

Prefer stable, frequently requested capabilities when linkloom has a user need
for them:

- Python engineering;
- CLI, FastAPI, and API design;
- Markdown parsing and structured data;
- RAG, text chunking, and retrieval evaluation;
- BM25, embeddings, hybrid retrieval, and reranking;
- Tool Calling and Structured Output;
- Agent State, Memory, and Checkpoint;
- tracing, evaluation, and bad-case analysis;
- human-in-the-loop, permission control, audit, and rollback;
- tests, Docker, CI, logging, and configuration management.

Introduce at most one or two major new technologies per milestone. Establish a
simple baseline before any comparison experiment.

MCP, multi-agent runtime, GraphRAG, fine-tuning, complex vector databases, and
desktop UI are optional research topics. Introduce them only when an explicit
user problem and evaluation method justify them; they must not block the main
product path.

### Required Daily Start Report

Before changing files, output the following in Chinese with these exact
headings:

#### 当前进度

State the current milestone, feature, completed evidence, and known failure or
missing record.

#### 今天只做什么

Describe the single task in one sentence.

#### 为什么现在做

Explain product value, learning value, and interview value separately.

#### Current Industry Relevance

Distinguish stable core, popular but optional, and unnecessary technology.

#### Likely Interview Questions

List three to five questions tied to this task.

#### Minimum Knowledge Boundary

State what must be explained, what only needs use-level understanding, and what
can be skipped.

#### 今天需要理解什么

Explain no more than two concepts with concrete linkloom examples.

#### Portfolio Evidence

Name the evidence the task will leave behind.

#### 准备修改什么

List the expected files.

#### 完成后能运行什么

Provide a copyable command.

#### 应该看到什么

Describe concrete output or behavior.

#### 如何验收

List objective success criteria.

#### 今天不做什么

State forbidden scope expansion.

#### 唯一确认项

Ask for no more than one decision. Prefer explicit options.

Stop after the report and wait for the user to reply `开始`. Do not modify files
before that approval.

### Implementation Rules

After the user replies `开始`:

1. implement only the confirmed minimum scope;
2. prefer the simplest readable and testable implementation;
3. do not add frameworks, future extension layers, or governance documents
   without a current requirement;
4. do not access or modify a real Obsidian vault without separate approval and
   the Vault Safety gates above;
5. run the relevant tests after implementation;
6. diagnose and explain a failure before retrying, and do not retry blindly;
7. do not start the next task or milestone automatically;
8. do not commit Git automatically.

### Required Completion Report

After implementation, output the following in Chinese with these exact
headings:

#### 今天实际完成了什么

Explain the result in non-technical language.

#### 修改了哪些文件

List actual files and their purpose.

#### 我应该亲自运行什么

Provide copyable commands.

#### 正常情况下会看到什么

Show the important output or behavior.

#### 测试与评测结果

List passed, failed, skipped, and unavailable checks.

#### 我必须能解释的两个点

Keep only the two most important concepts from the task.

#### 面试时怎么讲

Provide a 30-second project explanation, three likely follow-up questions with
concise reference answers, and the current capability boundary that must not be
overstated.

#### Learning Check

Ask the user to explain the task in their own words first. After the user
answers, identify gaps and simulate three interview follow-ups from basic to
deep. Do not require source memorization or complex mathematics.

#### 下一步建议

Propose one next task and explain why it follows. Do not execute it.

### Progress Maintenance

After each completed task, update only the current feature's `task.md` with:

- completed work;
- test results;
- learning points;
- interview evidence;
- remaining issues;
- one candidate next task.

Do not create daily progress documents. Keep product milestones only in
`docs/PRODUCT_ROADMAP.md`; keep long-term collaboration rules only in
`AGENTS.md`.

### Plain-Language Fallback

If the user says `我没听懂`, `说人话`, or expresses equivalent confusion:

- stop implementation;
- do not repeat the same definition;
- use an everyday analogy;
- show one concrete linkloom input, processing path, and output;
- finish with a one-sentence summary.

### Loss-Of-Control Guards

- If work exceeds 120 minutes, split it before implementation.
- If a task mainly adds documents, abstraction, or frameworks without a
  runnable result, pause and shrink it.
- If multiple milestones are active, stop and select one.
- If evidence cannot prove completion, report the task as incomplete.
- If the current feature has no accepted SPEC, implementation plan, or task
  record, stop at the missing gate instead of writing code.
