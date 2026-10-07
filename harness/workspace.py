"""Disposable copies of the target repository and the final diff.

`prepare_vendor` clones the target once. `create_run` exports the pinned
commit twice per run: a read-only `baseline/` and the agent's `workspace/`.
Neither copy contains `.git`, so nothing in a run can push, merge or rewrite
history. The diff is computed by the harness from the two copies.
"""

import difflib
import io
import os
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path

IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}


class WorkspaceError(RuntimeError):
    pass


def _git(*args, cwd=None):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=600)
    if result.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def prepare_vendor(task):
    """Clone the target repository (once) and make sure the pinned commit exists."""
    path = task.vendor_path
    if not (path / ".git").is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--quiet", task.url, str(path))
    try:
        _git("cat-file", "-e", f"{task.commit}^{{commit}}", cwd=path)
    except WorkspaceError:
        _git("fetch", "--quiet", "origin", cwd=path)
        _git("cat-file", "-e", f"{task.commit}^{{commit}}", cwd=path)
    return path


def export_commit(task, destination):
    """Write the files of the pinned commit (no .git) into destination."""
    vendor = task.vendor_path
    if not (vendor / ".git").is_dir():
        raise WorkspaceError(f"target not prepared ({vendor} missing); run: python -m harness prepare")
    archive = _git("archive", "--format=tar", task.commit, cwd=vendor)
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter="data")


def make_read_only(root):
    for current, dirs, files in os.walk(root):
        for name in files + dirs:
            path = Path(current) / name
            if not path.is_symlink():
                mode = path.stat().st_mode
                path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    root.chmod(root.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def make_writable(root):
    root.chmod(root.stat().st_mode | stat.S_IWUSR)
    for current, dirs, files in os.walk(root):
        for name in files + dirs:
            path = Path(current) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IWUSR)


def create_run(task, runs_dir, label=None):
    """Create runs/<id>/ with a read-only baseline and a writable workspace."""
    run_id = label or time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(runs_dir) / run_id
    if run_dir.exists():
        raise WorkspaceError(f"{run_dir} already exists")
    run_dir.mkdir(parents=True)
    export_commit(task, run_dir / "baseline")
    export_commit(task, run_dir / "workspace")
    make_read_only(run_dir / "baseline")
    return run_id, run_dir


def remove_run(run_dir):
    run_dir = Path(run_dir)
    if (run_dir / "baseline").exists():
        make_writable(run_dir / "baseline")
    shutil.rmtree(run_dir)


def _files(root):
    found = {}
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        for name in sorted(files):
            full = Path(current) / name
            found[full.relative_to(root).as_posix()] = full
    return found


def _lines(path):
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (UnicodeDecodeError, FileNotFoundError):
        return None


def diff_trees(baseline, workspace):
    """Return (changes, patch). changes: list of {"path", "change"}."""
    before, after = _files(Path(baseline)), _files(Path(workspace))
    changes, chunks = [], []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old and new and old.read_bytes() == new.read_bytes():
            continue
        change = "added" if old is None else "deleted" if new is None else "modified"
        changes.append({"path": path, "change": change})
        old_lines = _lines(old) if old else []
        new_lines = _lines(new) if new else []
        if old_lines is None or new_lines is None:
            chunks.append(f"Binary files a/{path} and b/{path} differ\n")
            continue
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile="/dev/null" if old is None else f"a/{path}",
            tofile="/dev/null" if new is None else f"b/{path}",
        )
        text = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff)
        chunks.append(text)
    return changes, "".join(chunks)
