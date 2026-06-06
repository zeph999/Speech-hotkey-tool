$ErrorActionPreference = "Stop"

$appName = "Speech Hotkey Tool"
$installRoot = Join-Path $env:LOCALAPPDATA "Programs\SpeechHotkeyTool"
$appDir = Join-Path $installRoot "app"
$sourceZip = Join-Path $PSScriptRoot "app.zip"
$tempDir = Join-Path $env:TEMP ("SpeechHotkeyToolInstall_" + [guid]::NewGuid().ToString("N"))
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "$appName.lnk"
$startMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$startMenuShortcut = Join-Path $startMenuDir "$appName.lnk"

function Show-Message($message, $seconds = 4) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $null = $shell.Popup($message, $seconds, $appName, 64)
    } catch {
        Write-Host $message
    }
}

if (-not (Test-Path $sourceZip)) {
    throw "Installer payload app.zip was not found."
}

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

try {
    Get-Process -Name "SpeechHotkeyTool", "SpeechHotkeyToolFast" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$installRoot*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue

    if (Test-Path $appDir) {
        Remove-Item -LiteralPath $appDir -Recurse -Force
    }

    Expand-Archive -LiteralPath $sourceZip -DestinationPath $tempDir -Force
    New-Item -ItemType Directory -Force -Path $appDir | Out-Null
    Copy-Item -Path (Join-Path $tempDir "*") -Destination $appDir -Recurse -Force

    $exePath = Join-Path $appDir "SpeechHotkeyToolFast.exe"
    if (-not (Test-Path $exePath)) {
        throw "Installed app executable was not found: $exePath"
    }

    $shell = New-Object -ComObject WScript.Shell

    $desktop = $shell.CreateShortcut($desktopShortcut)
    $desktop.TargetPath = $exePath
    $desktop.WorkingDirectory = $appDir
    $desktop.Description = $appName
    $desktop.Save()

    $start = $shell.CreateShortcut($startMenuShortcut)
    $start.TargetPath = $exePath
    $start.WorkingDirectory = $appDir
    $start.Description = $appName
    $start.Save()

    Start-Process -FilePath $exePath -WorkingDirectory $appDir
    Show-Message "Speech Hotkey Tool installed and started."
} finally {
    if (Test-Path $tempDir) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
