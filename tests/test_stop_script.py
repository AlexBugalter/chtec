import os
import subprocess
import sys
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
        # A fixed sleep can race native Qt startup (measured 3.4 s on this host).
        # Wait for this process or its venv child to expose an actual window.
        readiness = r"""
        $RootPid = ROOT_PID
        $Deadline = [DateTime]::UtcNow.AddSeconds(12)
        do {
            $Children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$RootPid"
            $Related = @($RootPid) + @($Children |
                Select-Object -ExpandProperty ProcessId)
            foreach ($RelatedId in $Related) {
                $Candidate = Get-Process -Id $RelatedId -ErrorAction SilentlyContinue
                if ($Candidate -and $Candidate.MainWindowHandle -ne 0) { exit 0 }
            }
            Start-Sleep -Milliseconds 100
        } while ([DateTime]::UtcNow -lt $Deadline)
        exit 1
        """.replace("ROOT_PID", str(process.pid))
        ready = subprocess.run(
            ["powershell", "-NoProfile", "-Command", readiness],
            capture_output=True,
            text=True,
            timeout=18,
        )
        assert ready.returncode == 0, "Dedicated native test window did not become ready"
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
