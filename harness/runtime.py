"""Runtime: validates, authorizes and dispatches model-requested tools."""

from pathlib import Path

from .policy import PermissionPolicy
from .registry import REGISTRY
from .sandbox import SandboxUnavailable
from .tools import ToolDenied

MAX_ARG_CHARS = 12_000


def ok(output):
    return {"status": "ok", "output": output}


def error(message):
    return {"status": "error", "output": message}


def denied(message):
    return {"status": "denied", "output": message}


class Runtime:
    """Validate and execute tool requests against one workspace and one sandbox."""

    def __init__(
        self,
        root,
        sandbox=None,
        mode="edit",
        enabled=None,
        writable=(),
        protected=(),
        checks=None,
        acceptance_dir=None,
        registry=REGISTRY,
    ):
        self.root = Path(root).resolve()
        self.registry = registry
        self.sandbox = sandbox
        self.policy = PermissionPolicy(registry, mode, enabled, writable, protected)
        self.acceptance_dir = acceptance_dir
        # The hidden acceptance check is for final verification only.
        self.agent_checks = {
            name: check for name, check in (checks or {}).items() if not check.needs_acceptance
        }
        self.commands = []  # every command actually run, with its exit code

    def describe_tools(self):
        """Tool descriptions for the system prompt: only permitted tools."""
        described = []
        for tool in self.policy.allowed_tools():
            description = tool.description
            if tool.name == "run_check":
                names = ", ".join(f"{n} ({c.description})" for n, c in self.agent_checks.items())
                description += f" Available checks: {names or 'none'}."
            if tool.mutates:
                description += f" Writable scope: {', '.join(self.policy.writable) or 'nothing'}."
            described.append({"name": tool.name, "args": list(tool.args), "description": description})
        return described

    def run_command(self, argv, mounts=()):
        if self.sandbox is None:
            raise SandboxUnavailable("no sandbox configured; commands are disabled")
        result = self.sandbox.run(argv, self.root, mounts=mounts)
        self.commands.append({"command": result["command"], "exit_code": result["exit_code"],
                              "timed_out": result["timed_out"]})
        return result

    def is_mutating(self, action):
        tool = self.registry.get(action.get("tool")) if isinstance(action, dict) else None
        return tool is None or tool.mutates

    def execute(self, action):
        """Validate one action, enforce policy and return a result object."""
        if not isinstance(action, dict) or set(action) != {"tool", "args"}:
            return error('action must be an object with exactly "tool" and "args"')
        name, args = action["tool"], action["args"]
        if not isinstance(name, str) or name not in self.registry:
            return error(f"unknown tool: {name!r}; available: {', '.join(t['name'] for t in self.describe_tools())}")
        tool = self.registry[name]

        if not isinstance(args, dict):
            return error("args must be an object")
        if set(args) != set(tool.args):
            return error(f"{name} takes exactly these arguments: {list(tool.args)}; got {sorted(args)}")
        for key, value in args.items():
            if not isinstance(value, str):
                return error(f"argument '{key}' must be a string")
            if len(value) > MAX_ARG_CHARS:
                return error(f"argument '{key}' exceeds {MAX_ARG_CHARS} characters")

        reason = self.policy.check(tool)
        if reason:
            return denied(reason)

        try:
            return ok(tool.func(self, **args))
        except ToolDenied as exc:
            return denied(str(exc))
        except SandboxUnavailable as exc:
            return error(f"sandbox unavailable, command not run: {exc}")
        except Exception as exc:
            return error(f"{type(exc).__name__}: {exc}")
