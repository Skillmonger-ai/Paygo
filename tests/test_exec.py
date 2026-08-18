"""End-to-end `paygo exec`: launch a real child under a budget.

These invoke the installed `paygo` console script as a subprocess so the full
path is exercised — run creation, session mint, localhost service, child launch
with injected env, and teardown.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PAYGO_BIN = Path(sys.executable).parent / "paygo"
ECHO_AGENT = REPO_ROOT / "examples" / "echo_agent.py"


def _run_exec(home: Path, budget: str, *command: str, extra_env: dict | None = None):
    env = os.environ.copy()
    env["PAYGO_HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(PAYGO_BIN), "exec", "-b", budget, "--", *command],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )


@pytest.mark.skipif(not PAYGO_BIN.exists(), reason="paygo console script not installed")
def test_exec_runs_child_and_injects_scoped_credentials(tmp_path: Path) -> None:
    result = _run_exec(tmp_path / "home", "5", sys.executable, str(ECHO_AGENT))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    # Wrapper header + spend summary are printed.
    assert "PAYGO" in out
    assert "Budget     $5.00" in out
    # The child could read its own balance via the injected token.
    assert "my budget: authorized $5.00" in out
    # But it could not administer the run, nor use a forged token.
    assert "top up my own budget -> HTTP 404" in out
    assert "forged token -> HTTP 401" in out


@pytest.mark.skipif(not PAYGO_BIN.exists(), reason="paygo console script not installed")
def test_exec_propagates_child_exit_code(tmp_path: Path) -> None:
    result = _run_exec(tmp_path / "home", "5", sys.executable, "-c", "import sys; sys.exit(3)")
    assert result.returncode == 3


@pytest.mark.skipif(not PAYGO_BIN.exists(), reason="paygo console script not installed")
def test_exec_rejects_non_positive_budget(tmp_path: Path) -> None:
    result = _run_exec(tmp_path / "home", "0", sys.executable, "-c", "pass")
    assert result.returncode != 0


@pytest.mark.skipif(not PAYGO_BIN.exists(), reason="paygo console script not installed")
def test_strict_mode_scrubs_provider_credentials(tmp_path: Path) -> None:
    # A child under --strict must not see a pre-existing provider key.
    child = "import os; print('KEY=' + repr(os.environ.get('OPENAI_API_KEY')))"
    env = os.environ.copy()
    env["PAYGO_HOME"] = str(tmp_path / "home")
    env["OPENAI_API_KEY"] = "sk-should-be-scrubbed"
    result = subprocess.run(
        [str(PAYGO_BIN), "exec", "-b", "5", "--strict", "--", sys.executable, "-c", child],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "KEY=None" in result.stdout


@pytest.mark.skipif(not PAYGO_BIN.exists(), reason="paygo console script not installed")
def test_non_strict_passes_environment_through(tmp_path: Path) -> None:
    child = "import os; print('KEY=' + repr(os.environ.get('OPENAI_API_KEY')))"
    result = _run_exec(
        tmp_path / "home", "5", sys.executable, "-c", child,
        extra_env={"OPENAI_API_KEY": "sk-passthrough"},
    )
    assert "KEY='sk-passthrough'" in result.stdout


@pytest.mark.skipif(not PAYGO_BIN.exists(), reason="paygo console script not installed")
def test_exec_spend_agent_exhausts_budget(tmp_path: Path) -> None:
    """M3 DoD: the agent buys until the ceiling, then is denied."""
    spend = REPO_ROOT / "examples" / "spend_agent.py"
    result = _run_exec(tmp_path / "home", "0.25", sys.executable, str(spend))
    assert result.returncode == 0, result.stderr + result.stdout
    out = result.stdout
    assert out.count("-$0.10") == 2
    assert "DENIED" in out
    assert "Spent       $0.20" in out
    assert "Remaining   $0.05" in out
