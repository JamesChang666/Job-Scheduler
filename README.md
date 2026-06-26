# Python / EXE Job Scheduler UI

A local Windows-friendly scheduler built with Python `tkinter`.

The UI is only the controller. A separate background agent runs the schedules, so jobs can continue after you close the UI. You can also install a Windows startup task so the agent starts again after reboot/login.

## Start the UI

```powershell
python scheduler_ui.py
```

## Install Background Startup

From PowerShell:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\install_scheduler_startup.ps1
```

This creates a Windows Task Scheduler entry named:

```text
PythonExeSchedulerAgent
```

It starts `scheduler_agent.py` when you log in.

## Remove Background Startup

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\uninstall_scheduler_startup.ps1
```

## Schedule Options

- `Weekly`: choose a weekday and time, such as Monday at `09:00`.
- `Monthly`: choose a day of the month and time, such as day 5 at `18:30`.
- `Every`: run at a fixed interval in seconds, minutes, or hours.
- `Run once`: use `YYYY-MM-DD HH:MM:SS`; the job disables itself after it runs.

## Features

- Choose a Python `.py` file or Windows `.exe` file.
- Add optional arguments.
- Add an optional end time so repeating jobs stop after a date/time.
- Set a working directory; blank uses the selected file folder.
- Run now, enable, disable, edit, and delete jobs.
- Background agent keeps schedules running after the UI closes.
- Startup task can relaunch the background agent after reboot/login.
- Execution output is saved in `logs/`.
- Job settings are saved in `jobs.json`.

## Notes

This is designed to keep working after you close the UI and after reboot once you log in. Running before any user logs in requires a true Windows Service, which is a heavier setup.
