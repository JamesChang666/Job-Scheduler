$StartupCmd = "C:\Users\james\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\PythonExeSchedulerAgent.cmd"
if (Test-Path $StartupCmd) {
    Remove-Item $StartupCmd -Force
    Write-Host "Removed Startup folder launcher:"
    Write-Host $StartupCmd
}
else {
    Write-Host "Startup folder launcher was not installed."
}
