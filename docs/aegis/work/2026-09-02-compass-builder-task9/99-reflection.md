# Compass Builder Task 9 reflection

## Outcome

Task 9 completed the authorized MVP release slice. The maintained controller now uses
remote-free, full-history worker clones outside the integration repository; workers edit
files only, while the controller validates scope, commits, imports exact refs, verifies,
and integrates serially. The package is installed and its exact installed copy passed
doctor plus sequential and two-worker parallel smoke tests.

## What the evidence changed

- Prompt-only Git ownership was insufficient on the managed host. Live observation—not
  speculation—drove the smallest durable correction: object-isolated clones and
  controller-owned commits.
- The initial Windows failure after that correction was CRLF policy drift, so clean-state
  validation was moved under the controller's hardened Git policy.
- A trivial installed parallel fixture was correctly rejected because coordination
  benefit was too low. Raising only the fixture's declared work complexity to the already
  calibrated medium class allowed a truthful two-builder smoke without bypassing policy.
- The five-pair result met the predefined win condition: 38.46% lower median wall-clock
  time, equal 5/5 first-pass acceptance, no interventions, and zero blocking safety
  metrics. It supports two builders only.

## Remaining boundaries

- Plugin Compass remains passive and read-only. Compass Builder is the write-capable
  executor selected by Codex when the story set justifies it.
- `auto` chooses sequential or parallel and then selects the lowest adequate supported
  effort for each story; low effort was used for the simple installed sequential story,
  while medium was required for the parallel stories.
- No higher concurrency limit, automatic conflict repair, unbounded retry, Git delivery,
  or cleanup was inferred. Completed installed-smoke clones remain available for review.
- A new Codex task is required to exercise implicit discovery of the newly installed
  skill version.

## Verification summary

- 223-test complete suite passed in 804.972 seconds.
- Repository audit reported full validation.
- Plugin Creator and Skill Creator passed for source and installed copies.
- HOL 3.0.18 passed policy and verification at grade A; the installed cachebuster version
  has one documented false-positive low SemVer finding and five optional-asset notices,
  with no Cisco findings.
- Installed-copy file parity was 89/89 with zero hash differences; doctor and both live
  smoke modes completed green with every recorded safety/intervention metric zero.
