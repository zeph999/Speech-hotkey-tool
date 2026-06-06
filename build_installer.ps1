param(
    [string]$SourceDir = "dist\SpeechHotkeyToolFast",
    [string]$OutputName = "SpeechHotkeyToolSetup.exe"
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$sourcePath = Join-Path $root $SourceDir
$installerDir = Join-Path $root "installer"
$stagingDir = Join-Path $installerDir "staging"
$outputDir = Join-Path $root "dist\installer"
$outputPath = Join-Path $outputDir $OutputName
$sedPath = Join-Path $installerDir "SpeechHotkeyTool.sed"

if (-not (Test-Path $sourcePath)) {
    throw "Source app folder not found: $sourcePath. Run .\build.ps1 -OneDir -Name SpeechHotkeyToolFast first."
}

New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

if (Test-Path $stagingDir) {
    Remove-Item -LiteralPath $stagingDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null

Copy-Item -LiteralPath (Join-Path $installerDir "install.cmd") -Destination $stagingDir -Force
Copy-Item -LiteralPath (Join-Path $installerDir "install.ps1") -Destination $stagingDir -Force

$payloadZip = Join-Path $stagingDir "app.zip"
if (Test-Path $payloadZip) {
    Remove-Item -LiteralPath $payloadZip -Force
}

Compress-Archive -Path (Join-Path $sourcePath "*") -DestinationPath $payloadZip -CompressionLevel Optimal -Force

$stagingDirForSed = $stagingDir.TrimEnd("\") + "\"
$sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3

[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$outputPath
FriendlyName=Speech Hotkey Tool Setup
AppLaunched=install.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=install.cmd
UserQuietInstCmd=install.cmd
SourceFiles=SourceFiles

[Strings]
FILE0="install.cmd"
FILE1="install.ps1"
FILE2="app.zip"

[SourceFiles]
SourceFiles0=$stagingDirForSed

[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
"@

Set-Content -LiteralPath $sedPath -Value $sed -Encoding ASCII

iexpress.exe /N /Q $sedPath

if (-not (Test-Path $outputPath)) {
    throw "Installer was not created: $outputPath"
}

Write-Host "Done. Output:"
Write-Host "  $outputPath"
