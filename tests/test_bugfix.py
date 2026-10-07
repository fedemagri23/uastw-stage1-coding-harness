"""Bug fix on the real target: the acceptance check fails on the starting code and
passes after the change; the existing tests still pass.

Uses the real repository copy, the real Docker sandbox and the real tools; only
the model is scripted (scripts/reference-fix.json), so the test is repeatable
and needs no model or API key. Skipped when the target or Docker is missing.
"""

import json
import unittest

from harness import config
from harness.controller import AgentController
from harness.model import ScriptedModel
from harness.runtime import Runtime
from harness.sandbox import DockerSandbox
from harness.verification import PASSED, verify
from harness.workspace import create_run, remove_run
from tests.helpers import docker_ready

TASK = config.load_task()
SETTINGS = config.load_settings()
SCRIPT = config.PROJECT_DIR / "scripts" / "reference-fix.json"


@unittest.skipUnless(docker_ready() and (TASK.vendor_path / ".git").is_dir(),
                     "target or sandbox not prepared (run: python -m harness prepare)")
class BugFixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_runs = config.PROJECT_DIR / "runs" / "_tests"
        cls.run_id, cls.run_dir = create_run(TASK, cls.tmp_runs, label=f"bugfix-{id(cls)}")
        cls.sandbox = DockerSandbox(SETTINGS.sandbox, cls.run_id)
        cls.runtime = Runtime(cls.run_dir / "workspace", sandbox=cls.sandbox, writable=TASK.writable,
                              protected=TASK.protected, checks=TASK.checks, acceptance_dir=TASK.acceptance_dir)
        cls.before = verify(cls.run_dir / "baseline", cls.run_dir / "baseline", TASK, cls.sandbox, cls.runtime.policy)
        model = ScriptedModel(json.loads(SCRIPT.read_text()))
        cls.outcome = AgentController(model, cls.runtime, SETTINGS.limits).run(TASK.request)
        cls.after = verify(cls.run_dir / "workspace", cls.run_dir / "baseline", TASK, cls.sandbox, cls.runtime.policy)

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.cleanup()
        remove_run(cls.run_dir)
        try:
            cls.tmp_runs.rmdir()
        except OSError:
            pass

    @staticmethod
    def status(report):
        return {c["name"]: c["status"] for c in report["checks"]}

    def test_acceptance_fails_on_the_starting_code(self):
        self.assertEqual(self.status(self.before)["acceptance"], "failed")
        self.assertEqual(self.status(self.before)["regression"], PASSED)

    def test_acceptance_passes_after_the_change(self):
        self.assertEqual(self.outcome["termination"], "final")
        self.assertEqual(self.status(self.after)["acceptance"], PASSED)

    def test_existing_tests_still_pass(self):
        self.assertEqual(self.status(self.after)["regression"], PASSED)
        self.assertEqual(self.after["verdict"], PASSED)

    def test_only_the_domain_model_changed(self):
        self.assertEqual(self.after["changes"], [{"path": "src/allocation/domain/model.py", "change": "modified"}])
        self.assertIn("+            if line in batch._allocations:", self.after["patch"])


if __name__ == "__main__":
    unittest.main()
