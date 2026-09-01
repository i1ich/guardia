# T4 risk spike: burst execution over a DynamoDB checkpointer

Validates the one architectural claim the project rests on: a LangGraph graph
can pause for a real gap between two separate Lambda invocations and resume
without re-running the node before the pause, billing only the seconds each
invocation actually runs.

Deployed 2026-09-01 to `sa-east-1` as `GuardiaSpikeStack` (Lambda
`guardia-t4-spike`), reusing `GuardiaStateStack`'s `guardia-checkpoints`
table. Code: [`infrastructure/lib/spike-stack.ts`](../infrastructure/lib/spike-stack.ts),
[`infrastructure/lambda/t4-spike/handler.py`](../infrastructure/lambda/t4-spike/handler.py).

## Setup

Off-the-shelf checkpointer: [`langgraph-checkpoint-aws`](https://pypi.org/project/langgraph-checkpoint-aws/)'s
`DynamoDBSaver`, tried first per the plan's fallback ordering. It worked, but
it hardcodes its own single-table key schema — partition key `PK`, sort key
`SK` — which doesn't match the `thread_id`/`checkpoint_id` names the plan
originally specified for `guardia-checkpoints`. The table's key schema was
changed to `PK`/`SK` to match the library rather than writing a custom
checkpointer (see `GuardiaStateStack` and its test). TTL attribute name
(`ttl`) was already correct.

Two-node graph: `node_a` (increments a DynamoDB counter as its side effect,
`SPIKE_COUNTER_<thread_id>` / `side_effect`, sharing the checkpoints table
without colliding with the checkpointer's own `CHECKPOINT_`/`CHUNK_`/`WRITES_`
key prefixes) → `node_b` (calls `interrupt()` first thing, so on resume the
whole node re-executes but `interrupt()` returns immediately with the cached
resume value instead of pausing again).

Invocation 1: `{"mode": "start", "thread_id": "..."}` — runs `node_a`, hits
the interrupt in `node_b`, returns `__interrupt__` payload.
Invocation 2: `{"mode": "resume", "thread_id": "...", "resume_value": "..."}`
— resumes `node_b` with the given value.

A forced cold start between invocations 1 and 2 was achieved with
`aws lambda update-function-configuration` (any config change invalidates
existing execution environments), rather than relying on natural idle-timeout
recycling within a fixed test window.

## Results against the four validation points

1. **Invocation 2 completes after a real ≥5-minute gap and a forced cold
   start.** ✅ Gap was ~7 minutes; the invocation-2 CloudWatch REPORT line
   shows `Init Duration: 2943.13 ms`, confirming a genuine cold start, not a
   reused warm environment.

2. **`node_a`'s side effect fires exactly once across both invocations.** ✅
   `SPIKE_COUNTER_<thread_id>` read `side_effect_count = 1` after invocation
   2. Invocation 2's result also showed `counter: 1` unchanged from
   invocation 1 — `node_a` was not replayed on resume, only `node_b` was.

3. **Total billed compute across both invocations < 5s.** ❌ Measured
   4656 ms (invocation 1, cold) + 4250 ms (invocation 2, forced cold) =
   **8.9 s total** — about 78% over budget. Root cause is import cost, not
   handler logic: `Init Duration` was ~3.0–3.7 s on every cold start
   regardless of memory size (tested 256 MB and 1024 MB — no meaningful
   difference, so it isn't CPU-bound compilation; it's the sheer size of the
   `langgraph` → `langchain-core` → `pydantic` v2 import graph, ~100 MB of
   vendored dependencies). A warm invocation is fast (low hundreds of ms,
   no `Init Duration` at all) — the cost is paid once per execution
   environment, not per invocation.

4. **The checkpoint item is inspectable and human-readable enough to debug
   an incident from the console.** ⚠️ Partial. The checkpoint metadata item
   (`PK=CHECKPOINT_<thread_id>`, `id`, `ns`, `parent_checkpoint_id`,
   `ref_loc`, `ttl`) is plain strings, directly readable — enough to see that
   an incident exists, its checkpoint chain, and when it expires. The actual
   state (`CHUNK_...` item, attribute `payload`) is a msgpack-encoded binary
   blob, not readable as-is in the console; it takes one `msgpack.unpackb()`
   call to decode. Good enough to confirm a paused incident and its shape;
   not good enough to eyeball the state values directly.

## Verdict: not a kill, a budget correction

The mechanism the architecture depends on — pause at `interrupt()`, survive
a real gap and a cold start, resume without replaying prior nodes — works.
Points 1, 2 are clean; point 4 is workable with a caveat. Point 3 is a real
miss, but the fix is revising an untested number written before any
measurement existed, not the architecture: a real incident's two invocations
(intake, then whenever a human responds — very likely both cold in
production, since the whole point of burst execution is long idle gaps) will
plausibly cost ~7–9 s billed compute, not <5 s. That is still Lambda-scale
cheap. Candidates for later, not blocking anything downstream:

- Revise the target to something measured (e.g. <10 s) rather than an
  a-priori guess.
- Investigate trimming the import graph, or Lambda SnapStart for Python if
  available in `sa-east-1`, if the number needs to come down.
- Do **not** reach for provisioned concurrency — it reintroduces the
  standing idle cost burst execution was chosen to avoid.

T5 and T9 (blocked on T4) can proceed.
