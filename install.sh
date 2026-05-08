#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.claude/skills" "$HOME/.claude/commands" "$HOME/.claude/goal"

ln -sfn "$ROOT/goal" "$HOME/.claude/skills/goal"
ln -sfn "$ROOT/goal.md" "$HOME/.claude/commands/goal.md"
chmod +x "$ROOT/goal/scripts/claude_goal.py"

python3 "$ROOT/goal/scripts/claude_goal.py" import-codex >/dev/null 2>&1 || true

echo "Installed /goal for Claude Code."
echo "Skill: $HOME/.claude/skills/goal"
echo "Command shim: $HOME/.claude/commands/goal.md"
echo "State DB: $HOME/.claude/goal/goals.sqlite"
