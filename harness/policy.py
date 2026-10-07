"""Permission policy: which tools may run and which files may change.

The same checks are used to choose the tools advertised in the prompt and
again at dispatch time, because the model can request a tool it was never
shown. Path patterns use fnmatch rules ("*" also matches "/").
"""

from fnmatch import fnmatchcase

MODES = ("read-only", "edit")


class PermissionPolicy:
    def __init__(self, registry, mode="edit", enabled=None, writable=(), protected=()):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.registry = registry
        self.mode = mode
        self.enabled = frozenset(registry if enabled is None else enabled)
        unknown = self.enabled - set(registry)
        if unknown:
            raise ValueError(f"unknown tools: {', '.join(sorted(unknown))}")
        self.writable = tuple(writable)
        self.protected = tuple(protected)

    def check(self, tool):
        """Return None when the tool may run, or a denial reason."""
        if tool.name not in self.enabled:
            return f"tool '{tool.name}' is not enabled"
        if tool.mutates and self.mode != "edit":
            return f"tool '{tool.name}' is not allowed in {self.mode} mode"
        return None

    def allowed_tools(self):
        return [tool for tool in self.registry.values() if self.check(tool) is None]

    def write_denial(self, relative):
        """Return None when relative (workspace path) may be changed, else a reason."""
        if any(fnmatchcase(relative, pattern) for pattern in self.protected):
            return f"{relative} is protected and cannot be changed"
        if not any(fnmatchcase(relative, pattern) for pattern in self.writable):
            return f"{relative} is outside the writable scope ({', '.join(self.writable) or 'nothing'})"
        return None
