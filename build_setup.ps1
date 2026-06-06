param(
    [string]$InnoCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "installer\SpeechHotkeyTool.iss"
$sourceDir = Join-Path $PSScriptRoot "dist\SpeechHotkeyToolFast"
$outputPath = Join-Path $PSScriptRoot "dist\installer\SpeechHotkeyToolSetup.exe"

if (-not (Test-Path $InnoCompiler)) {
    throw "Inno Setup compiler not found: $InnoCompiler"
}

if (-not (Test-Path $sourceDir)) {
    throw "Folder-based app not found: $sourceDir. Run .\build.ps1 -OneDir -Name SpeechHotkeyToolFast first."
}

New-Item -ItemType Directory -Force -Path (Split-Path $outputPath -Parent) | Out-Null

& $InnoCompiler $scriptPath

if (-not (Test-Path $outputPath)) {
    throw "Setup installer was not created: $outputPath"
}

Write-Host "Done. Output:"
Write-Host "  $outputPath"
