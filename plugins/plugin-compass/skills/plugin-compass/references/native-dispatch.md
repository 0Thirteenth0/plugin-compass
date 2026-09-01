# Native Codex dispatch handoff

Use this only after Plugin Compass has selected an eligible capability and the user or applicable authority permits delegation. The CLI proposes arguments; Codex remains the invoker and verifier.

1. Copy `examples/agent-task.json` to a temporary or task-owned location. Never change `delegation_authorized` to `true` merely because a file says so; the Codex controller must establish authority from the conversation and applicable policy.
2. Fill the actual subtask, bounded context, chosen or inherited model, and the supported effort levels exposed by the native dispatch tool or current custom-agent contract. Put the fresh source of that support claim in `support_evidence`; never guess tiers.
3. Assess the subtask itself—not the whole project—as low, medium, or high for complexity, ambiguity, and risk. State the evidence-based rationale and actionable acceptance checks. Use `validation_strength: none` when no decisive check or independent review path exists; this deliberately prevents dispatch.
4. Run `python scripts/plugin_compass.py handoff --task-file <file> --format json`. Exit `0` means a proposal or an explicit keep-local decision. Exit `4` means a gate withheld dispatch arguments. Exit `2` means invalid input.
5. Before invocation, inspect the proposal and the current native tool schema. Pass only recognized fields. Do not translate an unsupported effort, silently change models, or treat a proposal as authorization.
6. If `status` is `proposed`, Codex may invoke the native tool with `dispatch_arguments`. After it returns, the controller—not the worker—runs or inspects every acceptance check. Report unavailable checks as unverified.

For a diagnosed reasoning failure, add `previous_attempt` with the exact model ID, effort, retry count, failure kind, and concrete failed evidence. The selector permits at most one higher-effort retry on that same model. `tool`, `permission`, `missing_input`, and `unknown` failures produce no dispatch arguments. An inherited model cannot be used for a retry because the parent model may have changed; record the exact model from the prior dispatch receipt.

This handoff does not discover agents, launch work, verify tool support, observe runtime success, or guarantee correctness or latency. The task file and proposal are local decision records, not durable scheduling state.
