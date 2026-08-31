from __future__ import annotations

import os
import re
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_COMPOSE_FILE = "deploy/docker-compose.acceptance.yml"
TEST_PROJECT_PREFIX = "rf-sensor-test-"
POSTGRES_USER = "rf_platform"
POSTGRES_PASSWORD = "change-me"
POSTGRES_DB = "rf_platform"

_PROJECT_RE = re.compile(r"^rf-sensor-test-[a-z0-9][a-z0-9-]{7,62}$")


@dataclass(frozen=True)
class AcceptanceInfra:
    project_name: str
    compose_file: str
    postgres_port: int
    nats_port: int

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
            f"@127.0.0.1:{self.postgres_port}/{POSTGRES_DB}"
        )

    @property
    def nats_url(self) -> str:
        return f"nats://127.0.0.1:{self.nats_port}"


def make_test_project_name() -> str:
    """Return a unique Compose project name reserved for destructive tests."""
    suffix = f"{os.getpid()}-{secrets.token_hex(4)}"
    return f"{TEST_PROJECT_PREFIX}{suffix}"


def validate_test_project_name(project_name: str) -> str:
    """Fail closed unless cleanup is scoped to an ephemeral test project."""
    if not project_name:
        raise RuntimeError("refusing Docker cleanup with an empty Compose project name")
    if project_name == "rf-sensor":
        raise RuntimeError("refusing Docker cleanup against operational Compose project rf-sensor")
    if not _PROJECT_RE.fullmatch(project_name):
        raise RuntimeError(
            "refusing Docker cleanup because Compose project is not clearly test-specific: "
            f"{project_name!r}"
        )
    return project_name


def compose_command(project_name: str, *args: str) -> list[str]:
    project_name = validate_test_project_name(project_name)
    return [
        "docker",
        "compose",
        "-f",
        TEST_COMPOSE_FILE,
        "--project-name",
        project_name,
        *args,
    ]


def cleanup_command(project_name: str) -> list[str]:
    return compose_command(project_name, "down", "-v", "--remove-orphans")


def _run(
    cmd: list[str], env: dict[str, str], timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, env=env, check=True, text=True, timeout=timeout)


def _capture(cmd: list[str], env: dict[str, str], timeout: int = 120) -> str:
    print("$", " ".join(cmd), flush=True)
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def _parse_compose_port(value: str) -> int:
    # Docker Compose commonly returns values such as "127.0.0.1:49154".
    # Bracketed IPv6 can appear on some hosts; split from the right for both.
    port_text = value.rsplit(":", 1)[-1].strip()
    try:
        port = int(port_text)
    except ValueError as exc:  # pragma: no cover - defensive for Docker CLI variations
        raise RuntimeError(f"unable to parse Docker Compose port output: {value!r}") from exc
    if port <= 0:
        raise RuntimeError(f"Docker Compose returned an invalid mapped port: {value!r}")
    return port


def compose_port(project_name: str, service: str, container_port: int, env: dict[str, str]) -> int:
    output = _capture(
        compose_command(project_name, "port", service, str(container_port)),
        env,
        timeout=60,
    )
    return _parse_compose_port(output)


def start_isolated_infra(env: dict[str, str], project_name: str | None = None) -> AcceptanceInfra:
    """Start isolated acceptance PostgreSQL/NATS and update env with mapped URLs.

    The only destructive operation here is `down -v`, and it is guarded so it can
    run solely against rf-sensor-test-* projects, never the operational project.
    """
    project_name = validate_test_project_name(project_name or make_test_project_name())
    env.update(
        {
            "RF_POSTGRES_USER": POSTGRES_USER,
            "RF_POSTGRES_PASSWORD": POSTGRES_PASSWORD,
            "RF_POSTGRES_DB": POSTGRES_DB,
            "RF_ACCEPTANCE_COMPOSE_FILE": TEST_COMPOSE_FILE,
            "RF_ACCEPTANCE_COMPOSE_PROJECT": project_name,
        }
    )
    _run(cleanup_command(project_name), env, timeout=120)
    _run(compose_command(project_name, "up", "-d", "--wait"), env, timeout=180)
    infra = AcceptanceInfra(
        project_name=project_name,
        compose_file=TEST_COMPOSE_FILE,
        postgres_port=compose_port(project_name, "postgres", 5432, env),
        nats_port=compose_port(project_name, "nats", 4222, env),
    )
    env["RF_DATABASE_URL"] = infra.database_url
    env["RF_NATS_URL"] = infra.nats_url
    return infra


def cleanup_isolated_infra(project_name: str, env: dict[str, str]) -> None:
    _run(cleanup_command(project_name), env, timeout=120)
