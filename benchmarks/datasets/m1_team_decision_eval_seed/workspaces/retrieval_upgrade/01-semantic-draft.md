---
date: 2026-02-03
status: superseded
authority: working_draft
---
# Retrieval Upgrade — Semantic Draft

## Problem

Exact-term queries were missing a few paraphrases in the Atlas Lantern search
sample. The draft proposed replacing the existing BM25 baseline with a pure
semantic index.

## Proposed Change

The draft proposed removing BM25 from the first release and routing every query
through embeddings. It treated the early synonym-query prototype as evidence
that the lexical path was no longer needed.

## Open Risks

The draft had not measured cache isolation or whether exact-term diagnostics
would remain available. It was superseded after the benchmark review.

## Supersession

The February 3 pure-semantic proposal was superseded by the February 14
approved phased-hybrid decision. It is retained only as historical proposal
evidence and is not the current retrieval policy.
