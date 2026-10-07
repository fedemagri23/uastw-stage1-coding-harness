"""Tool registry: one data entry per tool instead of a chain of conditions.

Each entry holds what the model needs (description, argument names) and what
the runtime needs (whether the tool changes files, and which function runs it).
"""

from dataclasses import dataclass
from typing import Callable

from . import tools


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: tuple
    func: Callable
    mutates: bool = False
    executes: bool = False


REGISTRY = {
    tool.name: tool
    for tool in [
        Tool("list_files", "List up to 300 file paths of the repository.", (), tools.list_files),
        Tool(
            "read_file",
            "Read a UTF-8 text file (bounded; shortened output is labelled).",
            ("path",),
            tools.read_file,
        ),
        Tool(
            "search_files",
            "Find an exact literal string (not a question) in repository files; returns "
            "path, line number and excerpt. Use one short word or code fragment.",
            ("query",),
            tools.search_files,
        ),
        Tool(
            "write_file",
            "Create a new file inside the writable scope. Fails if the file already exists.",
            ("path", "content"),
            tools.write_file,
            mutates=True,
        ),
        Tool(
            "edit_file",
            "Replace `old` with `new` in a file inside the writable scope, only when `old` occurs "
            "exactly once. Copy `old` exactly from read_file output, including leading spaces; "
            "write every line of `new` with its full indentation. Python edits that do not "
            "compile are rejected.",
            ("path", "old", "new"),
            tools.edit_file,
            mutates=True,
        ),
        Tool(
            "run_tests",
            "Run pytest on one test file or directory under tests/ inside the sandbox; returns "
            "exit code and output.",
            ("path",),
            tools.run_tests,
            executes=True,
        ),
        Tool(
            "run_check",
            "Run a configured check by name inside the sandbox; returns exit code and output.",
            ("name",),
            tools.run_check,
            executes=True,
        ),
    ]
}
