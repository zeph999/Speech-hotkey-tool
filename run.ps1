$ErrorActionPreference = "Stop"

$candidatePythons = @(
    (Join-Path $PSScriptRoot ".venv\Scripts\python.exe"),
    (Join-Path $PSScriptRoot ".venv313\Scripts\python.exe")
)

$venvPython = $null
$lastError = $null
foreach ($candidate in $candidatePythons) {
    if (-not (Test-Path $candidate)) {
        continue
    }

    try {
        $versionOutput = & $candidate --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $venvPython = $candidate
            break
        }
        $lastError = $versionOutput
    }
    catch {
        $lastError = $_.Exception.Message
    }
}

if ($null -eq $venvPython) {
    throw "No runnable virtual environment found. Create one with Python 3.12 or 3.13, then run: python -m pip install -r requirements.txt. Last error: $lastError"
}

& $venvPython (Join-Path $PSScriptRoot "main.py") @args
exit $LASTEXITCODE
