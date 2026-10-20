# LangGraph decision-agent reference

This is an executable, deterministic reference—not a production migration of
LinkLoom Runtime V2. It uses a local fake model, an in-memory source-search tool,
`InMemorySaver` for thread checkpoints, and `InMemoryStore` for workspace-scoped
long-term decision hints. A proposed decision pauses at a human approval
interrupt; only approval allows the Store write. A second thread then reads the
stored decision, while the fake model still cites source evidence rather than
memory.

Run from the repository root:

```powershell
python -m pip install -r examples/langgraph_decision_agent/requirements.txt
python -m examples.langgraph_decision_agent.app
```

The integration test exercises the tool node, reducer, conditional routing,
same-`thread_id` interrupt/resume, and cross-thread Store lookup:

```powershell
pytest tests/integration/test_langgraph_reference_example.py -q
```

All state and memory are in-process fixtures. Nothing calls a model provider or
reads or writes a real vault.
