"""Contained execution in Docker. Skipped when Docker or the sandbox image is missing."""

import os
import unittest

from harness import config
from harness.sandbox import DockerSandbox
from tests.helpers import docker_ready, make_repo, remove

SETTINGS = config.load_settings().sandbox


@unittest.skipUnless(docker_ready(), "docker or the sandbox image is not available (run: python -m harness prepare)")
class DockerSandboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = make_repo()
        self.sandbox = DockerSandbox(SETTINGS, f"test-{os.getpid()}")

    def tearDown(self):
        self.sandbox.cleanup()
        remove(self.tmp)

    def run_py(self, code, **kwargs):
        return self.sandbox.run(["python", "-c", code], self.root, **kwargs)

    def test_nonzero_exit_code_and_output_stay_visible(self):
        result = self.run_py("import sys; print('failing check'); sys.exit(4)")
        self.assertEqual(result["exit_code"], 4)
        self.assertIn("failing check", result["output"])

    def test_workspace_is_visible_but_read_only(self):
        result = self.run_py("print(open('src/shop/pricing.py').read()); open('src/new.py', 'w')")
        self.assertIn("def total", result["output"])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("Read-only file system", result["output"])
        self.assertFalse((self.root / "src/new.py").exists())

    def test_host_files_outside_the_workspace_are_not_mounted(self):
        result = self.run_py("import os; print(os.path.exists('/work/../outside.txt'), os.listdir('/'))")
        self.assertIn("False", result["output"])
        self.assertNotIn("outside.txt", result["output"])

    def test_no_network(self):
        code = ("import socket\ntry:\n    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
                "    print('NETWORK OPEN')\nexcept OSError as e:\n    print('blocked', e)")
        result = self.run_py(code)
        self.assertIn("blocked", result["output"])
        self.assertNotIn("NETWORK OPEN", result["output"])

    def test_host_environment_and_credentials_are_not_passed(self):
        os.environ["STAGE1_FAKE_API_KEY"] = "sk-should-not-leak"
        try:
            result = self.run_py("import os; print(sorted(os.environ))")
        finally:
            del os.environ["STAGE1_FAKE_API_KEY"]
        self.assertNotIn("STAGE1_FAKE_API_KEY", result["output"])
        self.assertNotIn("sk-should-not-leak", result["output"])

    def test_stuck_command_is_killed_and_its_container_removed(self):
        result = self.run_py("import time; print('started', flush=True); time.sleep(120)", timeout=4)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])
        self.assertEqual(self.sandbox.leftover_containers(), [])

    def test_large_output_is_bounded(self):
        result = self.run_py("print('z' * 500000)")
        self.assertTrue(result["truncated"])
        self.assertIn("output shortened", result["output"])
        self.assertLessEqual(len(result["output"]), SETTINGS.output_bytes + 200)


if __name__ == "__main__":
    unittest.main()
