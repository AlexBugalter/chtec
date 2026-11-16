param(
    [switch]$Force,
    [switch]$ListOnly,
    [switch]$SelfTest,
    [int]$TargetProcessId = 0
)

$ErrorActionPreference = 'Stop'

function Test-ChtecProcess {
    param($Candidate)
    if ($Candidate.Name -ieq 'chtec.exe') {
        return $true
    }
    if ($Candidate.Name -notin @('python.exe', 'pythonw.exe')) {
        return $false
    }
    return [bool]($Candidate.CommandLine -match '(?i)^(?:"[^"]+"|\S+)\s+(?:(?:-u|-B|-I|-E|-s|-S)\s+)*-m\s+chtec(?:\s|$)')
}

if ($SelfTest) {
    $Cases = @(
        @{ Name = 'python.exe'; CommandLine = '"E:\App\.venv\Scripts\python.exe" -m chtec'; Expected = $true },
        @{ Name = 'pythonw.exe'; CommandLine = 'pythonw.exe -m chtec scan.pdf'; Expected = $true },
        @{ Name = 'chtec.exe'; CommandLine = 'chtec.exe'; Expected = $true },
        @{ Name = 'python.exe'; CommandLine = 'python.exe tools\diagnose_ui.py'; Expected = $false },
        @{ Name = 'python.exe'; CommandLine = 'python.exe -m chtec_other'; Expected = $false },
        @{ Name = 'python.exe'; CommandLine = 'python.exe -m pytest'; Expected = $false },
        @{ Name = 'python.exe'; CommandLine = 'python.exe script.py " -m chtec"'; Expected = $false },
        @{ Name = 'cmd.exe'; CommandLine = 'cmd /c python -m chtec'; Expected = $false }
    )
    foreach ($Case in $Cases) {
        if ((Test-ChtecProcess ([pscustomobject]$Case)) -ne $Case.Expected) {
            throw "Incorrect process selection: $($Case.CommandLine)"
        }
    }
    Write-Output 'Selection tests passed'
    exit 0
}

$Candidates = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='chtec.exe'" |
    Where-Object { Test-ChtecProcess $_ })
if ($TargetProcessId -ne 0) {
    # A Windows venv python.exe can be a launcher: the actual Qt window belongs
    # to its Python child. Include only descendants which also run chtec.
    $SelectedIds = [System.Collections.Generic.HashSet[int]]::new()
    [void]$SelectedIds.Add($TargetProcessId)
    do {
        $Added = $false
        foreach ($Candidate in $Candidates) {
            if ($SelectedIds.Contains([int]$Candidate.ParentProcessId)) {
                if ($SelectedIds.Add([int]$Candidate.ProcessId)) { $Added = $true }
            }
        }
    } while ($Added)
    $Candidates = @($Candidates | Where-Object { $SelectedIds.Contains([int]$_.ProcessId) })
}

if ($ListOnly) {
    $Candidates | Select-Object ProcessId, Name
    exit 0
}
if ($Candidates.Count -eq 0) {
    Write-Output 'Chtec is not running.'
    exit 0
}
foreach ($Candidate in $Candidates) {
    try {
        $TaskProcess = [System.Diagnostics.Process]::GetProcessById($Candidate.ProcessId)
        if ($Force) {
            Stop-Process -Id $Candidate.ProcessId -Force
            Write-Output "Stopped Chtec PID $($Candidate.ProcessId). Unsaved changes were discarded."
        } elseif ($TaskProcess.CloseMainWindow()) {
            Write-Output "Close requested for Chtec PID $($Candidate.ProcessId). Check its save confirmation."
        } else {
            Write-Output "No responsive window for Chtec PID $($Candidate.ProcessId). Use stop.bat /force if needed."
        }
    } catch [System.ArgumentException] {
        Write-Output "Chtec PID $($Candidate.ProcessId) has already exited."
    }
}
