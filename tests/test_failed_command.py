"""Failed command: a nonzero exit and its output stay visible; nothing claims checks passed."""

import json
import shutil
import sys
import unittest

from harness.config import TaskSpec
from harness.controller import AgentController
from harness.model import ScriptedModel
from harness.policy import PermissionPolicy
from harness.registry import REGISTRY
from harness.sandbox import run_bounded
from harness.verification import PASSED, verify
from tests.helpers import CHECKS, PROTECTED, WRITABLE, FakeSandbox, make_repo, make_runtime, remove, limits

FAILING_OUTPUT = "FAILED tests/test_pricing.py::test_total - assert 4 == 3\n1 failed in 0.01s"


def task_for(tmp):
    return TaskSpec(name="demo", url="", commit="0" * 40, request="fix", acceptance_dir=tmp,
                    writable=WRITABLE, protected=PROTECTED, checks=CHECKS)


class FailedCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = make_repo()

    def tearDown(self):
        remove(self.tmp)

    def test_nonzero_exit_code_and_output_are_captured(self):
        result = run_bounded([sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"],
                             timeout=30, max_bytes=1000)
        self.assertEqual(result["exit_code"], 3)
        self.assertIn("boom", result["output"])
        self.assertFalse(result["timed_out"])

    def test_agent_sees_the_failure_with_exit_code_and_output(self):
        sandbox = FakeSandbox(exit_code=1, output=FAILING_OUTPUT)
        model = ScriptedModel([{"tool": "run_check", "args": {"name": "regression"}}, {"final": "done"}])
        runtime = make_runtime(self.root, sandbox)
        AgentController(model, runtime, limits()).run("run the tests")

        seen = json.loads(model.calls[1][-1]["content"])["result"]
        self.assertEqual(seen["status"], "ok")  # the tool worked; the tests failed
        self.assertEqual(seen["output"]["exit_code"], 1)
        self.assertFalse(seen["output"]["passed"])
        self.assertIn("1 failed", seen["output"]["output"])
        self.assertEqual(runtime.commands[0]["exit_code"], 1)

    def test_verification_reports_failure_even_when_the_model_claims_success(self):
        sandbox = FakeSandbox(exit_code=1, output=FAILING_OUTPUT)
        model = ScriptedModel([{"final": "All tests pass. Verified."}])
        outcome = AgentController(model, make_runtime(self.root, sandbox), limits()).run("fix")
        self.assertEqual(outcome["final"], "All tests pass. Verified.")

        policy = PermissionPolicy(REGISTRY, writable=WRITABLE, protected=PROTECTED)
        report = verify(self.root, self.root, task_for(self.tmp), sandbox, policy)

        self.assertEqual(report["verdict"], "not verified")
        failed = [c for c in report["checks"] if c["name"] in ("regression", "acceptance")]
        self.assertTrue(all(c["status"] == "failed" and c["exit_code"] == 1 for c in failed))
        self.assertIn("1 failed", failed[0]["output"])

    def test_unavailable_sandbox_is_reported_not_passed(self):
        policy = PermissionPolicy(REGISTRY, writable=WRITABLE, protected=PROTECTED)
        report = verify(self.root, self.root, task_for(self.tmp), FakeSandbox(unavailable=True), policy)
        statuses = {c["name"]: c["status"] for c in report["checks"]}
        self.assertEqual(statuses["regression"], "unavailable")
        self.assertEqual(statuses["acceptance"], "unavailable")
        self.assertNotEqual(report["verdict"], PASSED)

    def test_timed_out_check_is_reported_as_timeout(self):
        policy = PermissionPolicy(REGISTRY, writable=WRITABLE, protected=PROTECTED)
        report = verify(self.root, self.root, task_for(self.tmp), FakeSandbox(timed_out=True), policy)
        self.assertEqual({c["status"] for c in report["checks"][1:]}, {"timeout"})
        self.assertNotEqual(report["verdict"], PASSED)

    def test_change_outside_the_scope_fails_verification(self):
        baseline = self.tmp / "baseline"
        shutil.copytree(self.root, baseline)
        (self.root / "tests/test_pricing.py").write_text("def test_total():\n    pass\n")
        policy = PermissionPolicy(REGISTRY, writable=WRITABLE, protected=PROTECTED)
        report = verify(self.root, baseline, task_for(self.tmp), FakeSandbox(), policy)
        self.assertEqual(report["checks"][0]["name"], "scope")
        self.assertEqual(report["checks"][0]["status"], "failed")
        self.assertIn("tests/test_pricing.py", report["checks"][0]["output"])
        self.assertNotEqual(report["verdict"], PASSED)

    def test_regression_uses_the_pristine_tests_and_acceptance_is_mounted_read_only(self):
        sandbox = FakeSandbox()
        policy = PermissionPolicy(REGISTRY, writable=WRITABLE, protected=PROTECTED)
        verify(self.root, self.root, task_for(self.tmp), sandbox, policy)
        mounts = {call["argv"][-1]: call["mounts"] for call in sandbox.calls}
        self.assertEqual([dst for _, dst in mounts["tests"]], ["/work/tests"])
        self.assertEqual([dst for _, dst in mounts["/acceptance"]], ["/acceptance"])


if __name__ == "__main__":
    unittest.main()
