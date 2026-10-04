# decision-patterns

Four ideas about **where to put a decision model (Jev, Kev, Laya…) inside an agent** and where code must stay in charge. Each pattern is a small, readable LangGraph graph with an interchangeable `Decider`: the same code runs against Jev, any server that copies `/v1/systemone`, or an offline scripted stand-in. It is a library: read the code and the tests, which are the runnable examples.

```bash
uv sync                               # from the repo root
uv run pytest decision-patterns       # 7 tests, no keys needed
```

`SystemOneDecider` takes its `base_url` and `api_key` as arguments and reads nothing from the environment.

| Pattern | Idea | Where code stays in charge |
|---|---|---|
| `router.py` | Pick the cheapest model that will do | Policy: when unsure, move up one model tier |
| `tool_gate.py` | Judge a tool call before it runs | Permissions, validation and spend limits **before** the model; block/human thresholds after |
| `cascade.py` | Cheap draft → verify with a decision → escalate on failure | Acceptance threshold and escalation cost |
| `core.py` | Typed contract (`Q`/`A`), confidence, `ScriptedDecider`, `SystemOneDecider` | – |

## The rule behind all four

> Code calculates and authorizes. The decision model judges. An LLM generates and reasons. Anything uncertain or irreversible goes to a human.

It comes from Jev 1.13's own limitations docs (arithmetic, counting and dates in code; state is treated as data, not as hostile) and from third-party security tests: **a typed format is not a security boundary**. That is why, in `tool_gate`, the injection question can only make the gate *stricter*, and irreversible tools keep a human step.

## Caveats

- The router's prices are **placeholders**: put your own.
- The tests use *scripted* rules, not a model: they demonstrate the architecture, not Jev's quality.
- `SystemOneDecider` has not been tested against a real server (only against an HTTP stub).
- The `choice_confidence` formula is the one independent audits found in Jev's Choice confidence; if TypeSafe changes it, adjust `core.py`.
