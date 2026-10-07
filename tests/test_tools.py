"""File tools: read, search and edit the intended files; reject paths outside the allowed area."""

import os
import unittest

from tests.helpers import FakeSandbox, make_repo, make_runtime, remove


def call(runtime, tool, **args):
    return runtime.execute({"tool": tool, "args": args})


class FileToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = make_repo()
        self.runtime = make_runtime(self.root)

    def tearDown(self):
        remove(self.tmp)

    # --- intended files ---------------------------------------------------

    def test_list_files_shows_visible_files_only(self):
        result = call(self.runtime, "list_files")
        self.assertEqual(result["status"], "ok")
        self.assertIn("src/shop/pricing.py", result["output"]["files"])
        self.assertNotIn(".secret", result["output"]["files"])

    def test_read_file_returns_content(self):
        result = call(self.runtime, "read_file", path="src/shop/pricing.py")
        self.assertEqual(result["status"], "ok")
        self.assertIn("def total(prices):", result["output"]["content"])
        self.assertFalse(result["output"]["truncated"])

    def test_search_finds_path_and_line(self):
        result = call(self.runtime, "search_files", query="def discount")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output"]["matches"], [
            {"path": "src/shop/pricing.py", "line": 5, "excerpt": "def discount(amount):"}])

    def test_edit_file_changes_only_the_intended_text(self):
        result = call(self.runtime, "edit_file", path="src/shop/pricing.py",
                      old="    return amount * 0.9\n", new="    return round(amount * 0.9, 2)\n")
        self.assertEqual(result["status"], "ok", result)
        text = (self.root / "src/shop/pricing.py").read_text()
        self.assertIn("return round(amount * 0.9, 2)", text)
        self.assertIn("return sum(prices)", text)

    def test_edit_requires_a_unique_match(self):
        result = call(self.runtime, "edit_file", path="src/shop/pricing.py", old="return", new="return  ")
        self.assertEqual(result["status"], "error")
        self.assertIn("occurs 2 times", result["output"])

    def test_edit_with_missing_text_shows_closest_lines(self):
        result = call(self.runtime, "edit_file", path="src/shop/pricing.py",
                      old="def totl(prices):", new="def total(items):")
        self.assertEqual(result["status"], "error")
        self.assertIn("Closest existing lines", result["output"])

    def test_edit_that_breaks_python_is_rejected_and_file_unchanged(self):
        before = (self.root / "src/shop/pricing.py").read_text()
        result = call(self.runtime, "edit_file", path="src/shop/pricing.py",
                      old="    return sum(prices)\n", new="return sum(prices)\n")
        self.assertEqual(result["status"], "error")
        self.assertIn("not be valid Python", result["output"])
        self.assertEqual((self.root / "src/shop/pricing.py").read_text(), before)

    def test_write_file_creates_new_file_but_never_overwrites(self):
        self.assertEqual(call(self.runtime, "write_file", path="src/shop/new.py", content="X = 1\n")["status"], "ok")
        again = call(self.runtime, "write_file", path="src/shop/new.py", content="X = 2\n")
        self.assertEqual(again["status"], "error")
        self.assertEqual((self.root / "src/shop/new.py").read_text(), "X = 1\n")

    # --- outside the allowed area ------------------------------------------

    def test_paths_outside_the_repository_are_denied(self):
        for path in ("../outside.txt", str(self.tmp / "outside.txt"), "/etc/passwd", "src/../../outside.txt"):
            with self.subTest(path=path):
                result = call(self.runtime, "read_file", path=path)
                self.assertEqual(result["status"], "denied", result)
                self.assertNotIn("host file", str(result["output"]))

    def test_hidden_files_are_denied(self):
        result = call(self.runtime, "read_file", path=".secret")
        self.assertEqual(result["status"], "denied")

    def test_symlink_pointing_outside_is_denied(self):
        os.symlink(self.tmp / "outside.txt", self.root / "src" / "link.txt")
        result = call(self.runtime, "read_file", path="src/link.txt")
        self.assertEqual(result["status"], "denied")
        self.assertIn("outside", result["output"])

    def test_protected_tests_cannot_be_edited(self):
        result = call(self.runtime, "edit_file", path="tests/test_pricing.py", old="== 3", new="== 4")
        self.assertEqual(result["status"], "denied")
        self.assertIn("protected", result["output"])
        self.assertIn("== 3", (self.root / "tests/test_pricing.py").read_text())

    def test_files_outside_the_writable_scope_cannot_be_changed(self):
        result = call(self.runtime, "edit_file", path="README.md", old="demo", new="hacked")
        self.assertEqual(result["status"], "denied")
        self.assertIn("outside the writable scope", result["output"])
        created = call(self.runtime, "write_file", path="setup.py", content="")
        self.assertEqual(created["status"], "denied")
        self.assertFalse((self.root / "setup.py").exists())

    def test_read_only_mode_denies_edits(self):
        runtime = make_runtime(self.root, mode="read-only")
        result = call(runtime, "edit_file", path="src/shop/pricing.py", old="0.9", new="0.8")
        self.assertEqual(result["status"], "denied")
        self.assertIn("read-only", result["output"])
        self.assertNotIn("edit_file", [t["name"] for t in runtime.describe_tools()])


class ExecutionToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = make_repo()

    def tearDown(self):
        remove(self.tmp)

    def test_run_tests_runs_pytest_in_the_sandbox_on_the_workspace(self):
        sandbox = FakeSandbox()
        result = call(make_runtime(self.root, sandbox), "run_tests", path="tests/test_pricing.py")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(sandbox.calls[0]["workspace"], str(self.root))
        self.assertEqual(sandbox.calls[0]["argv"][-1], "tests/test_pricing.py")

    def test_run_tests_rejects_paths_outside_tests_or_the_repository(self):
        sandbox = FakeSandbox()
        runtime = make_runtime(self.root, sandbox)
        self.assertEqual(call(runtime, "run_tests", path="src/shop/pricing.py")["status"], "denied")
        self.assertEqual(call(runtime, "run_tests", path="../outside.txt")["status"], "denied")
        self.assertEqual(sandbox.calls, [])

    def test_hidden_acceptance_check_is_not_available_to_the_agent(self):
        sandbox = FakeSandbox()
        result = call(make_runtime(self.root, sandbox), "run_check", name="acceptance")
        self.assertEqual(result["status"], "error")
        self.assertEqual(sandbox.calls, [])


if __name__ == "__main__":
    unittest.main()
