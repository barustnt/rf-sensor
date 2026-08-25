from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker is required for M2 acceptance")
def test_milestone2_operational_acceptance() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{Path.cwd() / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    subprocess.run(
        [sys.executable, "scripts/run_milestone2_acceptance.py"],
        cwd=Path.cwd(),
        env=env,
        check=True,
        timeout=600,
    )
