$ErrorActionPreference = "Stop"

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = "C:\Users\james\AppData\Local\PythonExeScheduler\start_agent.ps1"
$StartupDir = "C:\Users\james\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
$StartupCmd = Join-Path $StartupDir "PythonExeSchedulerAgent.cmd"

if (-not (Test-Path $Launcher)) {
    powershell.exe -ExecutionPolicy Bypass -File (Join-Path $AppDir "install_scheduler_startup.ps1")
}

New-Item -ItemType Directory -Path $StartupDir -Force | Out-Null
Set-Content -Path $StartupCmd -Encoding ASCII -Value "@echo off`r`npowershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`"`r`n"

Write-Host "Installed Startup folder launcher:"
Write-Host $StartupCmd
Write-Host ""
Write-Host "The scheduler agent will start automatically after reboot/login."
