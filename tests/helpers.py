"""Shared test helpers: a small fake repository and a fake sandbox."""

import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

from harness import config
from harness.config import Check, Limits
from harness.runtime import Runtime

WRITABLE = ("src/*.py", "src/**/*.py")
PROTECTED = ("tests/**",)

CHECKS = {
    "regression": Check("regression", "existing tests", ("python", "-m", "pytest", "-q", "tests")),
    "acceptance": Check("acceptance", "hidden check", ("python", "-m", "pytest", "/acceptance"), True),
}

FILES = {
    "README.md": "# demo repo\n",
    "src/shop/pricing.py": textwrap.dedent('''\
        def total(prices):
            return sum(prices)


        def discount(amount):
            return amount * 0.9
        '''),
    "tests/test_pricing.py": "from shop.pricing import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n",
    ".secret": "TOKEN=abc\n",
}


def make_repo():
    """Create a temporary repository copy; returns (tmpdir, root)."""
    tmp = Path(tempfile.mkdtemp(prefix="stage1-test-"))
    root = tmp / "workspace"
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tmp / "outside.txt").write_text("host file that must stay private\n")
    return tmp, root


def remove(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


class FakeSandbox:
    """Records commands and returns a configured result instead of running Docker."""

    def __init__(self, exit_code=0, output="1 passed", timed_out=False, unavailable=False):
        self.exit_code, self.output, self.timed_out, self.unavailable = exit_code, output, timed_out, unavailable
        self.calls = []

    def run(self, argv, workspace, mounts=(), timeout=None):
        from harness.sandbox import SandboxUnavailable

        if self.unavailable:
            raise SandboxUnavailable("docker not available (fake)")
        self.calls.append({"argv": list(argv), "workspace": str(workspace), "mounts": list(mounts)})
        return {"command": " ".join(argv), "exit_code": None if self.timed_out else self.exit_code,
                "output": self.output, "truncated": False, "output_bytes": len(self.output),
                "timed_out": self.timed_out, "duration_s": 0.01}


def make_runtime(root, sandbox=None, mode="edit", **kwargs):
    return Runtime(root, sandbox=sandbox or FakeSandbox(), mode=mode, writable=WRITABLE,
                   protected=PROTECTED, checks=CHECKS, acceptance_dir=root.parent, **kwargs)


def limits(**overrides):
    return Limits(**{**vars(Limits()), **overrides})


def docker_ready(image=None):
    """True when docker works and the sandbox image exists."""
    image = image or config.load_settings().sandbox.image
    if not shutil.which("docker"):
        return False
    try:
        probe = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0
