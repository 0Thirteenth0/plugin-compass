# Repository validation

The repository harness provides deterministic validation using only repository files,
Python 3.11 or newer, and Git. Run it from any working directory; subprocesses are bound
to the repository root with argument arrays, so the Windows path containing spaces is
supported.

## Profiles

```powershell
python scripts/check_repo_harness.py --profile docs
python scripts/check_repo_harness.py --profile contracts
python scripts/check_repo_harness.py --profile unit
python scripts/check_repo_harness.py --profile integration
python scripts/check_repo_harness.py --profile audit
```

| Profile | Focused checks | Repository-wide suite | Claim when green |
| --- | --- | --- | --- |
| `docs` | Required UTF-8 documentation paths and `git diff --check` | No | Focused repository validation |
| `contracts` | `tests.test_builder_models`, including schema/fixture shape and canonical bytes | Yes | Full repository validation |
| `unit` | Focused deterministic Builder unit modules, including paired comparator and runner mechanics | Yes | Full repository validation |
| `integration` | Worker verification, serial integration, cleanup, and isolated controller clones | Yes | Full repository validation |
| `audit` | Documentation, contracts, integration, harness self-tests, and `git diff --check` | Yes | Full repository validation |

Every behavior profile (`contracts`, `unit`, `integration`, and `audit`) runs its focused
checks first and the complete discovery suite last. The harness continues after a failed
focused check so the receipt records both focused and repository-wide evidence.

Use `--format json` or the equivalent `--json` flag for stable machine-readable output:

```powershell
python scripts/check_repo_harness.py --profile audit --format json
```

JSON uses schema version `plugin-compass.repo-harness.v1`, fixed check ordering, stable
commands and repository-relative paths, and no timestamp, duration, or captured process
trace. Human output includes a failed command's captured output for diagnosis.

Status and exit behavior are fail-closed:

| Status | Exit | Meaning |
| --- | ---: | --- |
| `passed` | `0` | Every scheduled check passed. A green behavior profile is full repository validation. |
| `failed` | `1` | At least one check ran and failed. This is not full validation. |
| `degraded` | `2` | At least one command was unavailable and recorded as skipped, with no executed failure. This is not full validation. |

Each failed or skipped check records its command, path/case, reason, and corrective
direction. A missing command is never silently ignored, and a degraded result is never
reported as full validation.

Every Python-backed check has a 900-second wall-clock timeout; the Git diff check has a
60-second timeout. The shared Compass Builder bounded-process owner continuously drains
both pipes, retains at most 1,048,576 bytes from each of stdout and stderr, and terminates
the owned process tree on timeout or output overflow. Either bound violation is a failed,
actionable check, never a skip or degraded result. Human failure output retains only the
bounded diagnostic; stable JSON intentionally omits process traces.

## Windows CI

`.github/workflows/validate.yml` uses a clean Windows runner with repository files,
standard Python, and Git. It runs these gates explicitly:

```powershell
python -m unittest tests.test_builder_models -v
python -m unittest discover -s tests -v
python -m unittest tests.test_builder_compare tests.test_builder_benchmark_runner -v
python -m unittest tests.test_builder_verifier tests.test_builder_integrator tests.test_builder_cleanup tests.integration.test_builder_worktrees -v
python -m unittest tests.test_repo_harness -v
git diff --check
```

The workflow intentionally does not call Plugin Creator, Skill Creator, an installed
plugin copy, DrSkill, HOL `plugin-scanner`, a live Codex worker, or any external package.

## Workstation-only local release gates

These gates are separate from repository validation because their tools or targets live
outside the checkout. Run them only on the named workstation and preserve their results
as separate release evidence. Their absence does not make `audit` degraded because
`audit` makes no claim about them.

The current repository and source target paths are:

```text
C:\Users\jiahu\Desktop\Plugin Compass
C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder
```

Plugin Creator package validation:

```powershell
python "C:\Users\jiahu\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py" "C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder"
```

Skill Creator focused-skill validation:

```powershell
python "C:\Users\jiahu\.codex\skills\.system\skill-creator\scripts\quick_validate.py" "C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder\skills\compass-builder"
```

HOL exact-target lint, verification, and scan:

```powershell
C:\Users\jiahu\.local\bin\plugin-scanner.cmd lint ./plugins/compass-builder
C:\Users\jiahu\.local\bin\plugin-scanner.cmd verify ./plugins/compass-builder
C:\Users\jiahu\.local\bin\plugin-scanner.cmd scan ./plugins/compass-builder --format json
```

The relative path and forward slashes are intentional: the local command wrapper invokes
the scanner inside WSL, where an absolute Windows path is not a valid target.

Installed-copy execution is an authorization-gated release check. The marketplace cache
location used for the validated Task 9 copy is:

```text
C:\Users\jiahu\.codex\plugins\cache\plugin-compass-local\compass-builder\0.1.0+codex.20260903002035
```

That exact installed directory contains 89 files whose relative paths and SHA-256 hashes
match the source package 89-for-89. Plugin Creator, Skill Creator, installed-directory
HOL scan, and `doctor` passed. The installed CLI completed a one-worker sequential smoke
at `low` effort in 64156 ms and a two-worker parallel smoke at `medium` effort in 102707
ms; both reached `completed` with every recorded safety and intervention metric zero.
Independent discovery runs passed 6 sequential-fixture tests and 12 parallel-fixture
tests. These installed-copy results complement rather than substitute for repository
validation. Start a new Codex task before relying on implicit skill discovery from this
new version.

HOL 3.0.18 reports grade A and policy/verification pass for the cachebuster version. Its
single low finding treats Plugin Creator's required `+codex.<cachebuster>` SemVer build
metadata as invalid; Plugin Creator validation accepts the version, and the base source
version scanned at 97 with no low findings before the supported cachebuster step. Five
additional informational notices concern optional undeclared interface assets.
