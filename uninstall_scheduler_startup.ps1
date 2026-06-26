$TaskName = "PythonExeSchedulerAgent"
schtasks.exe /Delete /TN $TaskName /F
Write-Host "Startup task removed: $TaskName"
