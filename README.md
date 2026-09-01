# guardia

An on-call triage agent. A CloudWatch alarm fires, and the agent reconstructs the
incident on its own — logs, metrics, deployment history, stack events, dependency
health — consults the operator's own runbooks, and returns ranked root-cause
hypotheses in which every factual claim cites a specific retrievable piece of
evidence. It proposes exactly one action and executes nothing without an explicit
human tap.

Mechanically, this is a durable state machine with a bounded evidence-gathering
loop and a human approval gate: it runs as short compute bursts against a
checkpointed state, so it can sit paused for hours between an alarm firing and a
human tapping approve while billing seconds, not hours. LangGraph is the current
implementation of that state machine, running on Lambda.

**Status:** early scaffold. Nothing is deployed yet.

## Layout

- `agent/` — the Python 3.12 LangGraph agent (graph nodes, tools wiring)
- `infrastructure/` — CDK (TypeScript) app: state, execution roles, intake, API
- `tools/` — read-only and mutating tool implementations used by the agent
- `evals/` — the eval harness and incident-corpus schema
- `docs/` — architecture notes
- `scripts/` — operational one-offs (deploy helpers, corpus promotion, etc.)

The incident corpus itself (real historical incidents, runbook sources) lives in
a private companion repository, since it contains operational details about the
subject systems.

## Security posture

Two directions, both structural rather than prompt-based:

- **Outbound:** no secrets and no user content reach the model. A redaction node
  runs before every model call.
- **Inbound:** log content is attacker-influenceable and is always treated as
  data, never as instruction. The agent can read logs and can act — the
  confused-deputy risk there is mitigated by IAM and node topology, not a prompt.

Autonomy is human-gated, permanently: mutating tools are reachable only
downstream of an explicit approval step.

## License

MIT — see [LICENSE](LICENSE).
