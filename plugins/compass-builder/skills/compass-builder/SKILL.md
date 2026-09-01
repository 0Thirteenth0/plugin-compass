---
name: compass-builder
description: Define the scaffold boundary for a planned write-capable companion using native Codex scheduling. Use when reviewing the Compass Builder package or its future sequential and parallel workflow contract.
license: MIT
---

# Compass Builder

Compass Builder is the planned write-capable companion to the read-only Plugin Compass advisor. It is intended to use native Codex scheduling for sequential or isolated parallel build workflows.

This scaffold establishes the plugin boundary only. Do not dispatch workers, create worktrees, mutate controller state, or claim that a build workflow is available until the controller and its versioned contracts are present and pass their required validation.
