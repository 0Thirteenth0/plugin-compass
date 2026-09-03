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

Report security issues using [SECURITY.md](SECURITY.md). License and adapted upstream
notices are in [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
