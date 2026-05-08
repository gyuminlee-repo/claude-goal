import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "goal" / "scripts" / "claude_goal.py"


def run_goal(tmp_path, *args, session="test-session"):
    env = os.environ.copy()
    env["CLAUDE_GOAL_DB"] = str(tmp_path / "goals.sqlite")
    env["CLAUDE_GOAL_SESSION_ID"] = session
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_set_status_pause_resume_complete(tmp_path):
    result = run_goal(tmp_path, "invoke", "--tokens", "98.5K", "improve benchmark coverage")
    assert result.returncode == 0, result.stderr
    assert "Action: set" in result.stdout
    assert "Token budget: 98.5K" in result.stdout
    assert "<objective>" in result.stdout

    result = run_goal(tmp_path, "pause")
    assert result.returncode == 0, result.stderr
    assert "Status: paused" in result.stdout

    result = run_goal(tmp_path, "resume")
    assert result.returncode == 0, result.stderr
    assert "Status: active" in result.stdout

    result = run_goal(tmp_path, "complete")
    assert result.returncode == 0, result.stderr
    assert "Status: complete" in result.stdout


def test_rejects_empty_and_duplicate_without_replace(tmp_path):
    result = run_goal(tmp_path, "set")
    assert result.returncode == 1
    assert "goal objective must not be empty" in result.stderr

    assert run_goal(tmp_path, "set", "first objective").returncode == 0
    result = run_goal(tmp_path, "set", "second objective")
    assert result.returncode == 1
    assert "already has a goal" in result.stderr

def test_json_output(tmp_path):
    assert run_goal(tmp_path, "set", "ship the thing").returncode == 0
    result = run_goal(tmp_path, "json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["objective"] == "ship the thing"
    assert data["status"] == "active"


def test_stop_hook_blocks_active_goal(tmp_path):
    assert run_goal(tmp_path, "set", "keep going").returncode == 0
    env = os.environ.copy()
    env["CLAUDE_GOAL_DB"] = str(tmp_path / "goals.sqlite")
    env["CLAUDE_GOAL_SESSION_ID"] = "test-session"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "stop-hook"],
        input=json.dumps({"session_id": "test-session", "stop_hook_active": False}),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["decision"] == "block"
    assert "<objective>" in data["reason"]


def test_stop_hook_allows_paused_goal(tmp_path):
    assert run_goal(tmp_path, "set", "keep going").returncode == 0
    assert run_goal(tmp_path, "pause").returncode == 0
    env = os.environ.copy()
    env["CLAUDE_GOAL_DB"] = str(tmp_path / "goals.sqlite")
    env["CLAUDE_GOAL_SESSION_ID"] = "test-session"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "stop-hook"],
        input=json.dumps({"session_id": "test-session"}),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_cli_finds_goal_after_session_id_drift(tmp_path):
    """A goal set under one session id must remain reachable from another.

    Repro for the bug where the Bash tool's PWD drifted between calls,
    keying CLI commands to a fresh cwd:<hash> session id while the live
    goal was still under the original one. status/pause/resume/clear
    were returning "no goal" while the Stop hook still saw it.
    """
    assert run_goal(tmp_path, "set", "stay alive", session="session-a").returncode == 0

    # Fresh "session" (simulates cwd drift) -> no env-keyed goal here, but
    # status must still surface the live one via the active-goal fallback.
    status = run_goal(tmp_path, "status", session="session-b")
    assert status.returncode == 0, status.stderr
    assert "stay alive" in status.stdout
    assert "Status: active" in status.stdout

    # pause from the drifted session should also work and actually pause
    # the original goal.
    paused = run_goal(tmp_path, "pause", session="session-b")
    assert paused.returncode == 0, paused.stderr
    assert "Status: paused" in paused.stdout

    # original session sees the paused state
    status_a = run_goal(tmp_path, "status", session="session-a")
    assert "Status: paused" in status_a.stdout

    # clear from the drifted session deletes the original goal
    cleared = run_goal(tmp_path, "clear", session="session-b")
    assert cleared.returncode == 0
    assert "Goal cleared." in cleared.stdout

    status_after = run_goal(tmp_path, "status", session="session-a")
    assert "No goal is currently set" in status_after.stdout


def test_stop_hook_finds_goal_via_active_fallback(tmp_path):
    """Stop hook must keep blocking even if every candidate session id misses.

    Set a goal under one session id, then send a hook payload with totally
    different session_id and cwd. The fallback to the most-recent active
    goal must still trigger the block.
    """
    assert run_goal(tmp_path, "set", "keep going", session="orig").returncode == 0

    env = os.environ.copy()
    env["CLAUDE_GOAL_DB"] = str(tmp_path / "goals.sqlite")
    # Force the script into a clean session-id space so the cwd-hash
    # fallback can't accidentally land on the goal.
    env["CLAUDE_GOAL_SESSION_ID"] = "drifted-session"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "stop-hook"],
        input=json.dumps({"session_id": "another-drifted", "cwd": "/totally/different/path"}),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["decision"] == "block"
    assert "keep going" in data["reason"]
