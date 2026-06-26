$ErrorActionPreference = "Stop"

Write-Host "Building one-file JobScheduler.exe..."
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --icon jcschedular.ico `
    --name JobScheduler `
    scheduler_ui.py

Write-Host ""
Write-Host "Build complete:"
Write-Host (Join-Path (Get-Location) "dist\JobScheduler.exe")
Write-Host ""
Write-Host "Run the EXE normally for the UI."
Write-Host "The same EXE runs the hidden background agent with: JobScheduler.exe --agent"
