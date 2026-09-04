# Compass Builder

Compass Builder coordinates verified sequential or isolated parallel Codex builders for
an approved repository story set. Plugin Compass remains read-only and supplies advisory
speed-first effort proposals; Compass Builder owns registered worker clones, bounded worker
launches, durable state, independent verification, serial integration, and benchmark
receipts.

## Use

Invoke the bundled `compass-builder` skill and provide an approved story set with
repository-relative write scopes and executable acceptance checks. `auto` chooses the
fastest safe mode; explicit `sequential` and `parallel` preferences never bypass safety
gates. The currently calibrated concurrency ceiling is two builders; a later release may
raise it only after the same safety and performance gates pass at the higher ceiling.

The deterministic command surface is documented in the repository's
`docs/COMPASS_BUILDER_CONTRACT.md`. Live `run`, paired `benchmark`, destructive
`cleanup`, plugin reinstall, and installed-copy execution require user authorization.
`run --dry-run`, `doctor`, `plan`, `verify-worker`, and `compare` do not launch a model
worker.

The paired `benchmark` command now emits separate synthetic token evidence alongside its
unchanged v1 receipts, aggregate, and comparison semantics. Attempt usage binds finalized
public controller events; missing usage stays explicitly incomplete. The token report
shows measured time, quality, retries, conflicts, interventions, and input/output token
trade-offs together while excluding warm-ups. It defines no prices, budget/routing rule,
or token-overhead graduation threshold and does not grant execution authority.

Outcome gates are opt-in through the separate closed
`compass-builder.plan-bundle.v2` contract. A v2 run requires a trusted in-process
`OperatorGateProvider`; the standalone CLI has no approval broker and therefore fails
closed before worker dispatch. The provider is consulted just in time for each exact
gate, workspace, story scope, and target SHA, and receives the complete validated gate
definition as well as its digest and the independently computed live platform/environment
identity. A manual gate's declared platform and environment digest must match that live
identity before provider consultation. Its closed `decisionState` is `approved`, `denied`,
`pending`, `abandoned`, `unavailable`, `blocked`, or manual-review-only `unmet`; only
`approved` can carry command authority. Missing or unreadable manual-review artifacts are
requested with a null artifact digest and can produce only a truthful `unavailable` receipt;
unsafe review paths fail before provider consultation. Command and manual-review decisions
are single-use, and repository files, worker output, or raw mappings cannot grant approval.
Before a command runs, the provider must atomically consume both approval IDs and retain
the exact attempt in durable provider-held history. Each intent has a scope-stable execution
key and a unique attempt key, so an unresolved attempt blocks same-scope replay while later
attempts cannot overwrite its history. The controller also records an immutable diagnostic
intent. The provider idempotently marks that exact attempt evidenced only after the sealed
receipt is authenticated, published, and checkpointed. An authenticated non-met receipt
completes the attempt and permits a separately approved fresh attempt. Persisted command
approval audits use the same closed D2 structure, validated without consuming authority or
consulting live files. Detached audit validation interprets executable syntax using the
recorded execution platform; live capability validation still requires the exact current-host
path and identity.
The provider must seal each full receipt with provider-held trust material and authenticate
the seal before journal publication or checkpointing. It explicitly initializes a new run
at a genesis checkpoint and retains a monotonic run-scoped receipt-count/terminal-digest
checkpoint plus durable initialization state.

## Safety

Workers are bound to one registered, full-history clone outside the integration
repository and one declared write scope. The clone has no remotes or alternate object
storage during execution. Nested
multi-agent support, inherited plugins, hooks, and user configuration are disabled while
project rules remain active. Headless workers use Codex's managed `--approve-for-me`
workspace profile. Workers edit files only: they may not mutate Git or create commits.
After each worker exits, the controller validates scope, creates exactly one story
commit, imports its exact branch SHA, and performs serial integration. The controller
rejects out-of-scope changes, stale heads, merge commits, failed checks, unclean trees,
and incomplete evidence. It never performs automatic conflict repair.

For v2, the first independent worker verification and required story gates run in the
registered isolated clone before its branch ref is imported. After each ordered merge,
the existing controller checks run first, then required root gates run at the exact merge
SHA before `integration-verified` or `lastVerifiedIntegrationSha` can advance. Gate
receipts are append-only and hash-chained. Only an exact durable `met` receipt can be
adopted after interruption when the active provider authenticates its seal and its live
platform/environment identity is unchanged. Authenticated non-met evidence requires a fresh
operator decision. An unauthenticated nonempty chain or tail beyond the provider checkpoint
blocks before any decision can extend it. A missing checkpoint is accepted only during the
provider's one-time empty-journal initialization; it never auto-anchors existing evidence,
and a lost checkpoint cannot silently reinitialize even if repository evidence was deleted.
The provider checkpoint detects missing or tail-deleted history, and the controller refolds
exact required story/root phase and target coverage before integration advancement and final
completion.

Report security issues using [SECURITY.md](SECURITY.md). License and adapted upstream
notices are in [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
