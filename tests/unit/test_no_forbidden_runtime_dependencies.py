from __future__ import annotations

from pathlib import Path


def test_no_agent_orchestrator_runtime_dependency() -> None:
    runtime_files = list(Path("src").rglob("*.py")) + [Path("pyproject.toml")]
    haystack = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)
    assert "agent-orchestrator" not in haystack.lower()
    assert "ao " not in haystack.lower()


def test_no_lab_ip_addresses_in_application_code() -> None:
    haystack = "\n".join(path.read_text(encoding="utf-8") for path in Path("src").rglob("*.py"))
    assert "10.10." not in haystack
