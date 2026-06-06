param(
    [string]$Name = "SpeechHotkeyTool",
    [switch]$Console,
    [switch]$OneDir
)

$ErrorActionPreference = "Stop"

Write-Host "Building $Name.exe ..."

$candidatePythons = @(
    (Join-Path $PSScriptRoot ".venv\Scripts\python.exe"),
    (Join-Path $PSScriptRoot ".venv313\Scripts\python.exe"),
    "python"
)

$python = $null
foreach ($candidate in $candidatePythons) {
    try {
        $versionOutput = & $candidate --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $python = $candidate
            break
        }
    }
    catch {
    }
}

if ($null -eq $python) {
    throw "No runnable Python found. Create a virtual environment and install requirements.txt first."
}

$windowMode = if ($Console) { "--console" } else { "--windowed" }
$bundleMode = if ($OneDir) { "--onedir" } else { "--onefile" }

& $python -m PyInstaller `
    --noconfirm `
    $bundleMode `
    $windowMode `
    --name $Name `
    --hidden-import sherpa_onnx `
    --collect-all sherpa_onnx `
    --collect-all sherpa_onnx_core `
    --hidden-import pystray `
    --hidden-import PIL `
    --collect-data opencc `
    main.py

Write-Host ""
Write-Host "Done. Output:"
if ($OneDir) {
    Write-Host "  dist\$Name\$Name.exe"
} else {
    Write-Host "  dist\$Name.exe"
}
