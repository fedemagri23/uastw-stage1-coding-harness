"""Initial context sent with the task: file list, scope and the files the task names."""

import re

PATH_PATTERN = re.compile(r"[\w./-]+\.(?:py|md|toml|txt|cfg|ini)\b")
MAX_CONTEXT_FILES = 3


def mentioned_files(runtime, text):
    """Workspace files whose relative path appears literally in text."""
    found = []
    for candidate in PATH_PATTERN.findall(text):
        candidate = candidate.strip("./")
        if candidate in found:
            continue
        result = runtime.execute({"tool": "read_file", "args": {"path": candidate}})
        if result["status"] == "ok":
            found.append(candidate)
    return found[:MAX_CONTEXT_FILES]


def build_context(runtime, task_text):
    listing = runtime.execute({"tool": "list_files", "args": {}})
    parts = []
    if listing["status"] == "ok":
        parts.append("Repository files (relative to the root):\n" + "\n".join(listing["output"]["files"]))
    parts.append("Writable scope: " + (", ".join(runtime.policy.writable) or "nothing"))
    for path in mentioned_files(runtime, task_text):
        content = runtime.execute({"tool": "read_file", "args": {"path": path}})["output"]
        note = f" ({content['note']})" if content.get("note") else ""
        parts.append(f"Current content of {path}{note}:\n{content['content']}")
    return "\n\n".join(parts)
