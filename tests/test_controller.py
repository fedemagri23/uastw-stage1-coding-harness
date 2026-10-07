"""Controller: scripted replies call the right tool; invalid requests never execute."""

import json
import unittest

from harness.context import build_context
from harness.controller import AgentController
from harness.model import ScriptedModel
from harness.registry import Tool
from harness.runtime import Runtime
from tests.helpers import make_repo, make_runtime, remove, limits


def observations(model):
    """The observation the model received before each of its calls (after the first)."""
    return [json.loads(call[-1]["content"]) for call in model.calls[1:]]


class SpyRegistry:
    """A registry with one recording tool, to prove whether anything executed."""

    def __init__(self):
        self.calls = []

        def spy(runtime, path):
            self.calls.append(path)
            return {"echo": path}

        self.registry = {"spy": Tool("spy", "Record the call.", ("path",), spy)}


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = make_repo()

    def tearDown(self):
        remove(self.tmp)

    def test_scripted_reply_calls_the_correct_tool_and_receives_its_result(self):
        model = ScriptedModel([
            {"tool": "read_file", "args": {"path": "src/shop/pricing.py"}},
            {"final": "read it"},
        ])
        outcome = AgentController(model, make_runtime(self.root), limits()).run("inspect pricing")

        self.assertEqual(outcome["termination"], "final")
        self.assertEqual(outcome["final"], "read it")
        seen = observations(model)[0]
        self.assertEqual(seen["tool"], "read_file")
        self.assertEqual(seen["result"]["status"], "ok")
        self.assertIn("def total(prices):", seen["result"]["output"]["content"])
        self.assertEqual(outcome["counters"]["executed"], 1)

    def test_tool_results_are_sent_as_untrusted_user_data(self):
        model = ScriptedModel([{"tool": "list_files", "args": {}}, {"final": "done"}])
        AgentController(model, make_runtime(self.root), limits()).run("list")
        last = model.calls[1][-1]
        self.assertEqual(last["role"], "user")
        self.assertIn("untrusted", json.loads(last["content"])["trust"])

    def test_system_prompt_lists_only_permitted_tools(self):
        model = ScriptedModel([{"final": "nothing to do"}])
        AgentController(model, make_runtime(self.root, mode="read-only"), limits()).run("look")
        system = model.calls[0][0]["content"]
        self.assertIn("- read_file (path)", system)
        self.assertNotIn("- edit_file", system)

    def test_context_is_sent_with_the_task(self):
        model = ScriptedModel([{"final": "ok"}])
        AgentController(model, make_runtime(self.root), limits()).run("fix it", context="Repository files: a.py")
        self.assertEqual(model.calls[0][1], {"role": "user", "content": "fix it\n\nRepository files: a.py"})

    def test_initial_context_includes_files_named_in_the_task_but_nothing_outside(self):
        context = build_context(make_runtime(self.root), "Fix discount in src/shop/pricing.py, see ../outside.txt")
        self.assertIn("src/shop/pricing.py", context.split("Writable scope")[0])
        self.assertIn("Current content of src/shop/pricing.py", context)
        self.assertIn("def discount(amount):", context)
        self.assertNotIn("host file", context)
        self.assertNotIn(".secret", context)


class InvalidRequestTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = make_repo()
        self.spy = SpyRegistry()
        self.runtime = Runtime(self.root, registry=self.spy.registry)

    def tearDown(self):
        remove(self.tmp)

    def run_one(self, reply):
        model = ScriptedModel([reply, {"final": "stop"}])
        outcome = AgentController(model, self.runtime, limits()).run("task")
        return outcome, observations(model)[0]["result"]

    def test_unknown_tool_gives_clear_error_without_executing(self):
        outcome, result = self.run_one({"tool": "delete_everything", "args": {}})
        self.assertEqual(result["status"], "error")
        self.assertIn("unknown tool: 'delete_everything'", result["output"])
        self.assertEqual(self.spy.calls, [])
        self.assertEqual(outcome["counters"]["errors"], 1)

    def test_missing_extra_or_non_string_arguments_are_rejected(self):
        for args, expected in [
            ({}, "takes exactly these arguments"),
            ({"path": "a", "mode": "x"}, "takes exactly these arguments"),
            ({"path": 7}, "must be a string"),
            ({"path": "x" * 20_000}, "exceeds"),
        ]:
            with self.subTest(args=args):
                _, result = self.run_one({"tool": "spy", "args": args})
                self.assertEqual(result["status"], "error")
                self.assertIn(expected, result["output"])
        self.assertEqual(self.spy.calls, [])

    def test_malformed_replies_are_counted_and_not_executed(self):
        for reply, expected in [
            ("not json at all", "not valid JSON"),
            ('["spy"]', "JSON object"),
            ('{"tool": "spy"}', "reply must be"),
            ('{"tool": "final", "args": {}}', "not a tool"),
            ('{"final": ""}', "non-empty"),
        ]:
            with self.subTest(reply=reply):
                outcome, result = self.run_one(reply)
                self.assertEqual(result["status"], "error")
                self.assertIn(expected, result["output"])
                self.assertEqual(outcome["counters"]["invalid_replies"], 1)
                self.assertEqual(outcome["counters"]["actions"], 0)
        self.assertEqual(self.spy.calls, [])

    def test_denied_action_is_counted_and_not_executed(self):
        tmp_runtime = make_runtime(self.root, mode="read-only")
        model = ScriptedModel([
            {"tool": "edit_file", "args": {"path": "src/shop/pricing.py", "old": "0.9", "new": "0.5"}},
            {"final": "stop"},
        ])
        outcome = AgentController(model, tmp_runtime, limits()).run("task")
        self.assertEqual(observations(model)[0]["result"]["status"], "denied")
        self.assertEqual(outcome["counters"]["denied"], 1)
        self.assertIn("0.9", (self.root / "src/shop/pricing.py").read_text())


if __name__ == "__main__":
    unittest.main()
