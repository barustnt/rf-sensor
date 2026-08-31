from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import acceptance_infra


def test_cleanup_guard_rejects_operational_project() -> None:
    for project_name in ["", "rf-sensor", "rf-sensor-prod", "rf-sensor-test-"]:
        with pytest.raises(RuntimeError):
            acceptance_infra.cleanup_command(project_name)


def test_cleanup_command_is_scoped_to_ephemeral_test_project() -> None:
    command = acceptance_infra.cleanup_command("rf-sensor-test-1234-abcd")

    assert command == [
        "docker",
        "compose",
        "-f",
        "deploy/docker-compose.acceptance.yml",
        "--project-name",
        "rf-sensor-test-1234-abcd",
        "down",
        "-v",
        "--remove-orphans",
    ]
    assert "deploy/docker-compose.infra.yml" not in command
    assert command[command.index("--project-name") + 1] != "rf-sensor"


def test_start_isolated_infra_uses_dynamic_ports_and_test_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[-3:] == ["port", "postgres", "5432"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="127.0.0.1:55432\n")
        if cmd[-3:] == ["port", "nats", "4222"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="127.0.0.1:54222\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(acceptance_infra.subprocess, "run", fake_run)
    env: dict[str, str] = {}

    infra = acceptance_infra.start_isolated_infra(env, project_name="rf-sensor-test-9999-abcd")

    assert infra.project_name == "rf-sensor-test-9999-abcd"
    assert infra.postgres_port == 55432
    assert infra.nats_port == 54222
    assert env["RF_DATABASE_URL"] == (
        "postgresql+asyncpg://rf_platform:change-me@127.0.0.1:55432/rf_platform"
    )
    assert env["RF_NATS_URL"] == "nats://127.0.0.1:54222"
    assert env["RF_ACCEPTANCE_COMPOSE_PROJECT"] == "rf-sensor-test-9999-abcd"
    assert env["RF_ACCEPTANCE_COMPOSE_FILE"] == "deploy/docker-compose.acceptance.yml"
    assert any(call[-3:] == ["down", "-v", "--remove-orphans"] for call in calls)
    assert any(call[-3:] == ["up", "-d", "--wait"] for call in calls)
    assert all("deploy/docker-compose.infra.yml" not in call for call in calls)
    assert all("rf-sensor" not in {item for item in call} for call in calls)


def test_cleanup_refuses_to_run_when_project_name_is_not_test_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(acceptance_infra.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError):
        acceptance_infra.cleanup_isolated_infra("rf-sensor", {})

    assert called is False


def test_acceptance_compose_file_has_no_operational_names_or_ports() -> None:
    compose = Path("deploy/docker-compose.acceptance.yml").read_text()

    assert "container_name" not in compose
    assert "rf_postgres_data" not in compose
    assert "127.0.0.1:5432:5432" not in compose
    assert "127.0.0.1:4222:4222" not in compose
    assert "127.0.0.1::5432" in compose
    assert "127.0.0.1::4222" in compose


def test_acceptance_scripts_do_not_cleanup_operational_compose_project() -> None:
    forbidden_project = '"--project-name",\n            "rf-sensor"'
    for path in [Path("scripts/run_demo.py"), Path("scripts/run_milestone2_acceptance.py")]:
        text = path.read_text()
        assert forbidden_project not in text
        assert '"deploy/docker-compose.infra.yml"' not in text
