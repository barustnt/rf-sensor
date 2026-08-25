from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker is required for the e2e demo")
def test_simulated_end_to_end_acceptance() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{Path.cwd() / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    subprocess.run(
        [sys.executable, "scripts/run_demo.py"],
        cwd=Path.cwd(),
        env=env,
        check=True,
        timeout=360,
    )
