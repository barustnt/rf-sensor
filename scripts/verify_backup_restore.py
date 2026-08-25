from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "-f",
    "deploy/docker-compose.infra.yml",
    "--project-name",
    "rf-sensor",
]
RESTORE_DB = "rf_platform_restore_check"
DB_DUMP_PATH = "/tmp/rf_platform_m2_restore_check.dump"


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(
        cmd,
        cwd=ROOT,
        env=os.environ.copy(),
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _postgres_shell(command: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return _run([*COMPOSE, "exec", "-T", "postgres", "sh", "-lc", command], timeout=timeout)


def _verify_postgres_restore() -> dict[str, Any]:
    _postgres_shell(
        f'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f {DB_DUMP_PATH}',
        timeout=180,
    )
    try:
        _postgres_shell(f'dropdb -U "$POSTGRES_USER" --if-exists {RESTORE_DB}')
        _postgres_shell(f'createdb -U "$POSTGRES_USER" {RESTORE_DB}')
        _postgres_shell(
            f'pg_restore -U "$POSTGRES_USER" -d {RESTORE_DB} {DB_DUMP_PATH}',
            timeout=180,
        )
        checks = {
            "sensors": "select count(*) from sensors;",
            "captures": "select count(*) from captures;",
            "analysis_jobs": "select count(*) from analysis_jobs;",
            "storage_snapshots": "select count(*) from storage_snapshots;",
            "retention_reports": "select count(*) from retention_reports;",
        }
        counts: dict[str, int] = {}
        for name, sql in checks.items():
            result = _postgres_shell(f'psql -U "$POSTGRES_USER" -d {RESTORE_DB} -tAc "{sql}"')
            counts[name] = int(result.stdout.strip())
        return {"database": RESTORE_DB, "counts": counts}
    finally:
        _postgres_shell(f'dropdb -U "$POSTGRES_USER" --if-exists {RESTORE_DB}')
        _postgres_shell(f"rm -f {DB_DUMP_PATH}")


def _verify_artifact_restore() -> dict[str, Any]:
    from rf_platform.common.config import get_settings

    settings = get_settings()
    artifact_root = (ROOT / settings.artifact_root).resolve()
    backup_dir = ROOT / ".data" / "backups"
    restore_dir = backup_dir / "artifact-restore-check"
    archive_path = backup_dir / "artifacts-restore-check.tar.gz"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(restore_dir, ignore_errors=True)
    if archive_path.exists():
        archive_path.unlink()

    with tarfile.open(archive_path, "w:gz") as archive:
        if artifact_root.exists():
            archive.add(artifact_root, arcname="artifacts")

    restore_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            destination = (restore_dir / member.name).resolve()
            if restore_dir.resolve() not in [destination, *destination.parents]:
                raise RuntimeError(f"unsafe archive member: {member.name}")
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeError(f"unable to extract artifact member: {member.name}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as handle:
                    shutil.copyfileobj(source, handle)

    restored_root = restore_dir / "artifacts"
    file_count = (
        sum(1 for path in restored_root.rglob("*") if path.is_file())
        if restored_root.exists()
        else 0
    )
    return {
        "artifact_root": str(artifact_root),
        "archive": str(archive_path),
        "restore_dir": str(restore_dir),
        "restored_file_count": file_count,
    }


def main() -> None:
    postgres = _verify_postgres_restore()
    artifacts = _verify_artifact_restore()
    result = {"postgres": postgres, "artifacts": artifacts}
    print(json.dumps(result, indent=2), flush=True)
    print("BACKUP RESTORE CHECK PASSED", flush=True)


if __name__ == "__main__":
    main()
