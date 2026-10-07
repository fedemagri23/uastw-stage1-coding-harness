"""Settings and task definition, loaded from TOML files.

settings.toml (optional, falls back to settings.example.toml) holds model,
limit and sandbox values. task/target.toml holds the target repository, the
exact starting commit, the request, the writable scope and the checks.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = PROJECT_DIR / "task" / "target.toml"
VENDOR_DIR = PROJECT_DIR / "vendor"
RUNS_DIR = PROJECT_DIR / "runs"


@dataclass(frozen=True)
class ModelSettings:
    provider: str = "ollama"
    name: str = "qwen2.5-coder:7b"
    endpoint: str = "http://127.0.0.1:11434/api/chat"
    timeout_s: int = 300
    num_ctx: int = 16384
    temperature: float = 0


@dataclass(frozen=True)
class Limits:
    max_actions: int = 20
    max_denied: int = 5
    max_invalid_replies: int = 4
    max_model_retries: int = 2
    observation_chars: int = 8000


@dataclass(frozen=True)
class SandboxSettings:
    image: str = "stage1-sandbox:py312"
    command_timeout_s: int = 120
    output_bytes: int = 16000
    memory: str = "1g"
    cpus: str = "2"
    pids: int = 256


@dataclass(frozen=True)
class Settings:
    model: ModelSettings = field(default_factory=ModelSettings)
    limits: Limits = field(default_factory=Limits)
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)
    source: str = "defaults"


@dataclass(frozen=True)
class Check:
    name: str
    description: str
    argv: tuple
    needs_acceptance: bool = False


@dataclass(frozen=True)
class TaskSpec:
    name: str
    url: str
    commit: str
    request: str
    acceptance_dir: Path
    writable: tuple
    protected: tuple
    checks: dict  # name -> Check

    @property
    def vendor_path(self):
        return VENDOR_DIR / self.name.replace("/", "__")


def _section(cls, data, name):
    values = data.get(name, {})
    known = set(cls.__dataclass_fields__)
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"unknown setting(s) in [{name}]: {', '.join(sorted(unknown))}")
    return cls(**values)


def load_settings(path=None):
    """Load settings.toml, else settings.example.toml, else built-in defaults."""
    candidates = [Path(path)] if path else [PROJECT_DIR / "settings.toml", PROJECT_DIR / "settings.example.toml"]
    for candidate in candidates:
        if candidate.is_file():
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
            return Settings(
                model=_section(ModelSettings, data, "model"),
                limits=_section(Limits, data, "limits"),
                sandbox=_section(SandboxSettings, data, "sandbox"),
                source=str(candidate),
            )
        if path:
            raise FileNotFoundError(f"settings file not found: {candidate}")
    return Settings()


def load_task(path=DEFAULT_TARGET, request_file=None):
    path = Path(path).resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    target, task = data["target"], data["task"]
    request_path = Path(request_file) if request_file else path.parent / task["request_file"]
    checks = {
        name: Check(
            name=name,
            description=spec.get("description", ""),
            argv=tuple(spec["argv"]),
            needs_acceptance=bool(spec.get("needs_acceptance", False)),
        )
        for name, spec in data.get("checks", {}).items()
    }
    return TaskSpec(
        name=target["name"],
        url=target["url"],
        commit=target["commit"],
        request=request_path.read_text(encoding="utf-8").strip(),
        acceptance_dir=(path.parent / task["acceptance_dir"]).resolve(),
        writable=tuple(task.get("writable", ())),
        protected=tuple(task.get("protected", ())),
        checks=checks,
    )
