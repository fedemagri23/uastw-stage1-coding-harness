"""Final verification: independent of anything the model claims.

After the loop stops (for any reason) the harness computes the diff, checks
that only files inside the writable scope changed, and re-runs every
configured check in the sandbox. Regression tests run from the pristine
baseline copy of tests/ (mounted over the workspace's tests/), and the
acceptance check is mounted read-only from outside the workspace. A check that
fails, times out or cannot run is reported as such; only all checks passing
gives the verdict "passed".
"""

from pathlib import Path

from .sandbox import SandboxUnavailable
from .workspace import diff_trees

PASSED, FAILED, TIMEOUT, UNAVAILABLE, ERROR = "passed", "failed", "timeout", "unavailable", "error"


def scope_check(changes, policy):
    problems = [f"{c['path']}: {policy.write_denial(c['path'])}" for c in changes if policy.write_denial(c["path"])]
    return {
        "name": "scope",
        "description": "Only files inside the writable scope changed",
        "status": FAILED if problems else PASSED,
        "output": "\n".join(problems) if problems else f"{len(changes)} changed file(s), all inside the writable scope",
    }


def run_check(check, workspace, baseline, task, sandbox):
    mounts = []
    if check.name == "regression" and (Path(baseline) / "tests").is_dir():
        mounts.append((Path(baseline) / "tests", "/work/tests"))
    if check.needs_acceptance:
        mounts.append((task.acceptance_dir, "/acceptance"))
    entry = {"name": check.name, "description": check.description}
    try:
        result = sandbox.run(list(check.argv), workspace, mounts=mounts)
    except SandboxUnavailable as exc:
        return {**entry, "status": UNAVAILABLE, "output": str(exc), "exit_code": None}
    except Exception as exc:
        return {**entry, "status": ERROR, "output": f"{type(exc).__name__}: {exc}", "exit_code": None}
    if result["timed_out"]:
        status = TIMEOUT
    else:
        status = PASSED if result["exit_code"] == 0 else FAILED
    return {
        **entry,
        "status": status,
        "command": result["command"],
        "exit_code": result["exit_code"],
        "duration_s": result["duration_s"],
        "truncated": result["truncated"],
        "output": result["output"],
    }


def verify(workspace, baseline, task, sandbox, policy, emit=None):
    """Return {"changes", "patch", "checks", "verdict"} for a workspace."""
    emit = emit or (lambda _event: None)
    changes, patch = diff_trees(baseline, workspace)
    checks = [scope_check(changes, policy)]
    emit({"event": "check", **checks[0]})
    for check in task.checks.values():
        emit({"event": "check_start", "name": check.name})
        entry = run_check(check, workspace, baseline, task, sandbox)
        checks.append(entry)
        emit({"event": "check", **entry})
    verdict = PASSED if all(c["status"] == PASSED for c in checks) else "not verified"
    return {"changes": changes, "patch": patch, "checks": checks, "verdict": verdict}


def summary_line(output, limit=160):
    """Last non-empty output line (pytest puts its summary there)."""
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    text = lines[-1] if lines else ""
    return text if len(text) <= limit else text[: limit - 3] + "..."
