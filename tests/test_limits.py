"""Limits: action, denial, invalid-reply, retry, output and time limits stop safely."""

import itertools
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from harness.controller import AgentController, bound_result
from harness.model import ModelError, ScriptedModel
from harness.registry import Tool
from harness.runtime import Runtime
from harness.sandbox import SHORTENED, run_bounded
from tests.helpers import make_repo, make_runtime, remove, limits

PY = sys.executable


class CountingRegistry:
    def __init__(self):
        self.calls = 0

        def count(runtime, path):
            self.calls += 1
            return {"path": path}

        self.registry = {"count": Tool("count", "Count calls.", ("path",), count)}


class ActionLimitTest(unittest.TestCase):
    def test_repeating_reply_reaches_the_limit_and_stops_further_actions(self):
        counting = CountingRegistry()
        runtime = Runtime(".", registry=counting.registry)
        model = ScriptedModel(itertools.repeat({"tool": "count", "args": {"path": "same"}}))

        outcome = AgentController(model, runtime, limits(max_actions=5)).run("loop forever")

        self.assertEqual(outcome["termination"], "action_limit")
        self.assertEqual(outcome["counters"]["actions"], 5)
        self.assertEqual(counting.calls, 1)  # repeats are blocked, not re-executed
        self.assertEqual(outcome["counters"]["blocked_repeats"], 4)
        self.assertEqual(len(model.calls), 6)  # the 6th request was refused, nothing ran after it

    def test_distinct_requests_also_stop_at_the_limit(self):
        counting = CountingRegistry()
        runtime = Runtime(".", registry=counting.registry)
        replies = ({"tool": "count", "args": {"path": f"file{i}"}} for i in itertools.count())
        outcome = AgentController(ScriptedModel(replies), runtime, limits(max_actions=3)).run("task")
        self.assertEqual(outcome["termination"], "action_limit")
        self.assertEqual(counting.calls, 3)

    def test_observation_reports_actions_left(self):
        counting = CountingRegistry()
        model = ScriptedModel([{"tool": "count", "args": {"path": "a"}}, {"final": "done"}])
        AgentController(model, Runtime(".", registry=counting.registry), limits(max_actions=4)).run("task")
        self.assertEqual(json.loads(model.calls[1][-1]["content"])["actions_left"], 3)


class OtherLimitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = make_repo()

    def tearDown(self):
        remove(self.tmp)

    def test_denied_actions_stop_the_run_at_the_limit(self):
        replies = ({"tool": "read_file", "args": {"path": f"../outside{i}"}} for i in itertools.count())
        outcome = AgentController(ScriptedModel(replies), make_runtime(self.root),
                                  limits(max_denied=3)).run("task")
        self.assertEqual(outcome["termination"], "denied_limit")
        self.assertEqual(outcome["counters"]["denied"], 3)

    def test_invalid_replies_stop_the_run_at_the_limit(self):
        model = ScriptedModel(itertools.repeat("this is not json"))
        outcome = AgentController(model, make_runtime(self.root), limits(max_invalid_replies=3)).run("task")
        self.assertEqual(outcome["termination"], "invalid_limit")
        self.assertEqual(outcome["counters"]["invalid_replies"], 3)
        self.assertEqual(outcome["counters"]["actions"], 0)

    def test_model_failures_are_retried_and_counted(self):
        model = ScriptedModel([ModelError("timeout"), ModelError("timeout"), {"final": "ok"}])
        outcome = AgentController(model, make_runtime(self.root), limits(max_model_retries=2)).run("task")
        self.assertEqual(outcome["termination"], "final")
        self.assertEqual(outcome["counters"]["model_retries"], 2)

    def test_model_failure_after_retries_stops_with_model_error(self):
        model = ScriptedModel(itertools.repeat(ModelError("connection refused")))
        outcome = AgentController(model, make_runtime(self.root), limits(max_model_retries=1)).run("task")
        self.assertEqual(outcome["termination"], "model_error")
        self.assertIn("connection refused", outcome["reason"])
        self.assertEqual(outcome["counters"]["model_calls"], 2)

    def test_ctrl_c_cancels_the_run_cleanly(self):
        model = ScriptedModel([KeyboardInterrupt()])
        outcome = AgentController(model, make_runtime(self.root), limits()).run("task")
        self.assertEqual(outcome["termination"], "cancelled")


class OutputLimitTest(unittest.TestCase):
    def test_large_command_output_is_bounded_and_marked_shortened(self):
        code = "print('HEAD'); print('x' * 200000); print('TAIL')"
        result = run_bounded([PY, "-c", code], timeout=30, max_bytes=2000)
        self.assertTrue(result["truncated"])
        self.assertLess(len(result["output"]), 2200)
        self.assertIn("output shortened", result["output"])
        self.assertTrue(result["output"].startswith("HEAD"))
        self.assertTrue(result["output"].rstrip().endswith("TAIL"))
        self.assertGreater(result["output_bytes"], 200000)

    def test_small_output_is_not_marked(self):
        result = run_bounded([PY, "-c", "print('hello')"], timeout=30, max_bytes=2000)
        self.assertFalse(result["truncated"])
        self.assertNotIn(SHORTENED.split(":")[0], result["output"])

    def test_large_observation_sent_to_the_model_is_bounded_and_marked(self):
        result = bound_result({"status": "ok", "output": {"content": "y" * 50_000}}, 1000)
        self.assertTrue(result["shortened"])
        self.assertLess(len(result["output"]), 1200)
        self.assertIn("observation shortened", result["output"])

    def test_large_file_read_is_bounded_and_marked(self):
        tmp, root = make_repo()
        try:
            (root / "src/big.py").write_text("# filler\n" * 5000)
            result = make_runtime(root).execute({"tool": "read_file", "args": {"path": "src/big.py"}})
            self.assertTrue(result["output"]["truncated"])
            self.assertIn("SHORTENED", result["output"]["note"])
        finally:
            remove(tmp)


class StuckCommandTest(unittest.TestCase):
    def test_timeout_kills_the_command_and_its_children(self):
        script = "import subprocess, sys, time\n" \
                 "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n" \
                 "print(child.pid, flush=True)\ntime.sleep(60)\n"
        result = run_bounded([PY, "-c", script], timeout=1.5, max_bytes=2000)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])
        child = int(result["output"].split()[0])
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_no_writes_happen_after_a_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            late = Path(tmp) / "late.txt"
            code = f"import time; time.sleep(2); open({str(late)!r}, 'w').write('late')"
            result = run_bounded([PY, "-c", code], timeout=0.5, max_bytes=2000)
            self.assertTrue(result["timed_out"])
            time.sleep(2.5)
            self.assertFalse(late.exists())

    def test_on_stop_hook_runs_before_killing(self):
        stopped = []
        run_bounded([PY, "-c", "import time; time.sleep(30)"], timeout=0.5, max_bytes=100,
                    on_stop=lambda: stopped.append(True))
        self.assertEqual(stopped, [True])


if __name__ == "__main__":
    unittest.main()
