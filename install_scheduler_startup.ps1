$ErrorActionPreference = "Stop"

$TaskName = "PythonExeSchedulerAgent"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Agent = Join-Path $AppDir "scheduler_agent.py"
$LocalAppData = "C:\Users\james\AppData\Local"
$LauncherDir = Join-Path $LocalAppData "PythonExeScheduler"
$Launcher = Join-Path $LauncherDir "start_agent.ps1"

$Python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    $Python = (Get-Command python.exe -ErrorAction Stop).Source
}

New-Item -ItemType Directory -Path $LauncherDir -Force | Out-Null

$LauncherContent = @"
`$ErrorActionPreference = "Stop"
Start-Process -FilePath "$Python" -ArgumentList '"$Agent"' -WorkingDirectory "$AppDir" -WindowStyle Hidden
"@
Set-Content -Path $Launcher -Value $LauncherContent -Encoding UTF8

$TaskRun = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""

Write-Host "Installing startup task: $TaskName"
Write-Host "Command: $TaskRun"

schtasks.exe /Create /TN $TaskName /SC ONLOGON /TR $TaskRun /F | Write-Host

Write-Host ""
Write-Host "Startup task installed. The scheduler agent will start when you log in."
Write-Host "Starting agent now..."
Start-Process powershell.exe -ArgumentList "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`"" -WindowStyle Hidden
