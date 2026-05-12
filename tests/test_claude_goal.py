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


def test_cli_does_not_leak_goals_across_sessions(tmp_path):
    """A goal set in session A must NOT surface for session B.

    Earlier versions of this script papered over cwd drift by falling
    back to "any active goal in the DB", which leaked a goal from one
    Claude session into another. The fix is to rely on a stable
    TERM_SESSION_ID anchor + candidate list — never global fallback.
    """
    assert run_goal(tmp_path, "set", "session A goal", session="session-a").returncode == 0

    status_b = run_goal(tmp_path, "status", session="session-b")
    assert status_b.returncode == 0, status_b.stderr
    assert "No goal is currently set" in status_b.stdout

    # Stop hook in session B with no overlapping candidates must NOT block.
    env = os.environ.copy()
    env["CLAUDE_GOAL_DB"] = str(tmp_path / "goals.sqlite")
    env["CLAUDE_GOAL_SESSION_ID"] = "session-b"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "stop-hook"],
        input=json.dumps({"session_id": "session-b", "cwd": "/different/path"}),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""  # no block when no goal in this session


def test_two_concurrent_terminals_do_not_share_goals(tmp_path):
    """Two Claude sessions in separate terminal tabs must stay isolated.

    Real-world repro: user runs Claude in tab A, sets a goal. User opens
    tab B and runs Claude there with no goal. Tab B's Stop hook used to
    fire telling it to keep working on tab A's goal. Each terminal tab
    has its own TERM_SESSION_ID, so under the fix tab B's candidate
    list never includes tab A's session id and the hook stays silent.
    """
    db = str(tmp_path / "goals.sqlite")

    # Tab A: sets a goal under its own iTerm session id.
    env_a = os.environ.copy()
    env_a["CLAUDE_GOAL_DB"] = db
    env_a.pop("CLAUDE_GOAL_SESSION_ID", None)
    env_a.pop("CLAUDE_SESSION_ID", None)
    env_a.pop("CLAUDE_CODE_SESSION_ID", None)
    env_a["TERM_SESSION_ID"] = "iterm-tab-A-uuid"
    env_a["PWD"] = "/Users/alice/proj-a"
    set_a = subprocess.run(
        [sys.executable, str(SCRIPT), "set", "tab A goal"],
        env=env_a, text=True, capture_output=True, check=False,
    )
    assert set_a.returncode == 0, set_a.stderr

    # Tab B: different TERM_SESSION_ID, different cwd, no goal of its own.
    env_b = os.environ.copy()
    env_b["CLAUDE_GOAL_DB"] = db
    env_b.pop("CLAUDE_GOAL_SESSION_ID", None)
    env_b.pop("CLAUDE_SESSION_ID", None)
    env_b.pop("CLAUDE_CODE_SESSION_ID", None)
    env_b["TERM_SESSION_ID"] = "iterm-tab-B-uuid"
    env_b["PWD"] = "/Users/alice/proj-b"

    status_b = subprocess.run(
        [sys.executable, str(SCRIPT), "status"],
        env=env_b, text=True, capture_output=True, check=False,
    )
    assert status_b.returncode == 0, status_b.stderr
    assert "No goal is currently set" in status_b.stdout
    assert "tab A goal" not in status_b.stdout

    # Tab B's Stop hook must stay silent — it has no goal of its own.
    hook_b = subprocess.run(
        [sys.executable, str(SCRIPT), "stop-hook"],
        input=json.dumps({"session_id": "claude-session-b", "cwd": "/Users/alice/proj-b"}),
        env=env_b, text=True, capture_output=True, check=False,
    )
    assert hook_b.returncode == 0, hook_b.stderr
    assert hook_b.stdout == "", f"Tab B hook leaked tab A's goal: {hook_b.stdout!r}"

    # Tab A's hook still finds its own goal.
    hook_a = subprocess.run(
        [sys.executable, str(SCRIPT), "stop-hook"],
        input=json.dumps({"session_id": "claude-session-a", "cwd": "/Users/alice/proj-a"}),
        env=env_a, text=True, capture_output=True, check=False,
    )
    assert hook_a.returncode == 0, hook_a.stderr
    data = json.loads(hook_a.stdout)
    assert data["decision"] == "block"
    assert "tab A goal" in data["reason"]


def test_term_session_anchors_goal_across_pwd_drift(tmp_path):
    """A goal set in one Claude session must remain reachable across cwd drift.

    Bash subshells inherit TERM_SESSION_ID even after `cd`. As long as the
    same TERM_SESSION_ID is present, CLI commands must resolve the same
    goal — no env-var override needed.
    """
    env = os.environ.copy()
    env["CLAUDE_GOAL_DB"] = str(tmp_path / "goals.sqlite")
    # Strip any explicit overrides so we exclusively test the TERM_SESSION_ID path.
    env.pop("CLAUDE_GOAL_SESSION_ID", None)
    env.pop("CLAUDE_SESSION_ID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env["TERM_SESSION_ID"] = "iterm-tab-abc-123"
    env["PWD"] = "/tmp/orig-cwd"

    set_result = subprocess.run(
        [sys.executable, str(SCRIPT), "set", "stay alive across drift"],
        env=env, text=True, capture_output=True, check=False,
    )
    assert set_result.returncode == 0, set_result.stderr

    # Now simulate cwd drift in a Bash subshell of the same Claude session.
    env["PWD"] = "/tmp/wandered-far-away"
    status_result = subprocess.run(
        [sys.executable, str(SCRIPT), "status"],
        env=env, text=True, capture_output=True, check=False,
    )
    assert status_result.returncode == 0, status_result.stderr
    assert "stay alive across drift" in status_result.stdout
    assert "Status: active" in status_result.stdout


def test_stop_hook_does_not_recover_legacy_cwd_goal_when_anchor_present(tmp_path):
    """Stop hook must NOT cwd-recover legacy goals when an authoritative anchor exists.

    Earlier behavior added cwd-hash candidates whenever the hook payload
    carried a cwd, so a legacy cwd-keyed goal could be picked up by any new
    Claude session that happened to live in the same repo. That was the
    real-world leak: a fresh Claude Code session opening in the same
    directory as a stale cwd-scheme goal kept inheriting it via stop-hook.

    With strict isolation, the presence of an authoritative session anchor
    (CLAUDE_CODE_SESSION_ID env or hook_data.session_id) suppresses cwd /
    term fallbacks. Legacy cwd-keyed goals stay invisible to new sessions
    and must be migrated explicitly (e.g. CLAUDE_GOAL_SESSION_ID=cwd:<hash>).
    """
    import hashlib
    real_cwd = "/Users/alice/proj-a"
    real_cwd_session_id = "cwd:" + hashlib.sha256(real_cwd.encode()).hexdigest()[:16]

    # Seed a legacy cwd-scheme goal in the DB.
    assert run_goal(tmp_path, "set", "legacy goal should stay isolated", session=real_cwd_session_id).returncode == 0

    # A new Claude session fires stop-hook from the same repo. The hook
    # payload carries that session's own anchor (NOT the legacy cwd hash),
    # plus the cwd. Strict isolation must reject the legacy match.
    env = os.environ.copy()
    env["CLAUDE_GOAL_DB"] = str(tmp_path / "goals.sqlite")
    env.pop("CLAUDE_GOAL_SESSION_ID", None)
    env.pop("CLAUDE_SESSION_ID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "stop-hook"],
        input=json.dumps({"session_id": "fresh-session-uuid", "cwd": real_cwd}),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # Strict isolation: stop-hook must produce no block decision.
    assert result.stdout.strip() == "", f"unexpected output: {result.stdout!r}"

    # Explicit migration path: providing the legacy cwd-hash via the
    # CLAUDE_GOAL_SESSION_ID override resolves the goal, so users can still
    # complete or pause stale goals deliberately.
    env_migrate = env.copy()
    env_migrate["CLAUDE_GOAL_SESSION_ID"] = real_cwd_session_id
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "status"],
        env=env_migrate,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "legacy goal should stay isolated" in result.stdout


def test_claude_code_session_id_isolates_same_repo_sessions(tmp_path):
    """Two Claude Code sessions in the same repo (same PWD) must stay isolated.

    On WSL2/Linux TERM_SESSION_ID is absent, so prior versions fell back to
    a PWD-derived sid — meaning two `claude` invocations in the same repo
    shared the same goal. Claude Code now exports a per-session UUID as
    CLAUDE_CODE_SESSION_ID; that env var must be the primary anchor and
    must suppress cwd/term fallbacks (otherwise the cwd hash would re-leak
    session A's goal into session B's stop hook).
    """
    db = str(tmp_path / "goals.sqlite")
    shared_pwd = str(tmp_path / "shared-repo")

    env_a = os.environ.copy()
    env_a["CLAUDE_GOAL_DB"] = db
    env_a.pop("CLAUDE_GOAL_SESSION_ID", None)
    env_a.pop("CLAUDE_SESSION_ID", None)
    env_a.pop("TERM_SESSION_ID", None)
    env_a.pop("ITERM_SESSION_ID", None)
    env_a["CLAUDE_CODE_SESSION_ID"] = "423ae752-1a08-4cc4-b779-aaaaaaaaaaaa"
    env_a["PWD"] = shared_pwd
    set_a = subprocess.run(
        [sys.executable, str(SCRIPT), "set", "session A goal"],
        env=env_a, text=True, capture_output=True, check=False,
    )
    assert set_a.returncode == 0, set_a.stderr

    env_b = dict(env_a)
    env_b["CLAUDE_CODE_SESSION_ID"] = "9f3b6c50-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    status_b = subprocess.run(
        [sys.executable, str(SCRIPT), "status"],
        env=env_b, text=True, capture_output=True, check=False,
    )
    assert status_b.returncode == 0, status_b.stderr
    assert "No goal is currently set" in status_b.stdout
    assert "session A goal" not in status_b.stdout

    hook_b = subprocess.run(
        [sys.executable, str(SCRIPT), "stop-hook"],
        input=json.dumps({"session_id": env_b["CLAUDE_CODE_SESSION_ID"], "cwd": shared_pwd}),
        env=env_b, text=True, capture_output=True, check=False,
    )
    assert hook_b.returncode == 0, hook_b.stderr
    assert hook_b.stdout == "", f"Session B hook leaked session A's goal: {hook_b.stdout!r}"

    set_b = subprocess.run(
        [sys.executable, str(SCRIPT), "set", "session B goal"],
        env=env_b, text=True, capture_output=True, check=False,
    )
    assert set_b.returncode == 0, set_b.stderr
    assert "session B goal" in set_b.stdout

    status_a = subprocess.run(
        [sys.executable, str(SCRIPT), "status"],
        env=env_a, text=True, capture_output=True, check=False,
    )
    assert "session A goal" in status_a.stdout
    assert "session B goal" not in status_a.stdout
