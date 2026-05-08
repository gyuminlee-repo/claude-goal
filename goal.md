---
description: Persist and continue a long-running Claude Code goal
argument-hint: "[status|pause|resume|clear|complete|list|import-codex|codex|replace] [--tokens N] <objective>"
allowed-tools: Bash(python3:*)
---

## Goal State

!`python3 ~/.claude/skills/goal/scripts/claude_goal.py invoke "$ARGUMENTS"`

Follow the returned Claude instructions exactly. If the goal is active, continue the work now. If it is paused, stopped, or missing, report that state concisely.
