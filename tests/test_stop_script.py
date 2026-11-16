import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows stop helper")
def test_stop_script_targets_only_chtec_processes():
    script = Path(__file__).resolve().parents[1] / "tools" / "stop_app.ps1"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-SelfTest",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Selection tests passed" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native close helper")
@pytest.mark.parametrize("force", [False, True])
def test_stop_script_closes_dedicated_native_app(tmp_path, force):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="windows", CHTEC_CONFIG=str(tmp_path / "config.json"))
    process = subprocess.Popen([sys.executable, "-m", "chtec"], cwd=root, env=env)
    try:
        time.sleep(2)
        assert process.poll() is None
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "tools" / "stop_app.ps1"),
            "-TargetProcessId",
            str(process.pid),
        ]
        if force:
            command.append("-Force")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert ("Stopped Chtec" if force else "Close requested") in result.stdout
        exit_code = process.wait(timeout=10)
        if not force:
            assert exit_code == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
