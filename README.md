# Python / EXE Job Scheduler UI

A local Windows-friendly scheduler built with Python `tkinter`.

The UI is only the controller. A separate background agent runs the schedules, so jobs can continue after you close the UI. You can also install a Windows startup task so the agent starts again after reboot/login.

## Start the UI

```powershell
python scheduler_ui.py
```

When running from source on Windows, install dependencies first:

```powershell
python -m pip install -r requirements.txt
```

This installs `tzdata`, which gives Python full timezone support on Windows.

## How To Use

1. Open the UI.
2. Click `New`.
3. Choose a Python `.py` file or Windows `.exe` file.
4. Choose `Run As`: `Python` for `.py`, `EXE` for `.exe`.
5. Optional: add arguments and a working directory.
6. Choose a schedule:
   - `Weekly` with one or more weekdays
   - `Monthly`
   - `Every` seconds/minutes/hours
   - `Run once`
7. Choose `Schedule Time Zone`, such as `Pacific/Auckland`, `Asia/Taipei`, or `America/New_York`.
   - Use `Reset` to return to the default timezone.
8. Optional: set `End Time` to stop a repeating job after a date/time.
9. Click `Save`.
10. Use `Run Now` to test immediately.
11. Check `logs/` and the right-side details panel for results.

The UI starts the background agent automatically. You can close the UI and the background agent keeps running.
Use `Start with Windows` in the toolbar to make the hidden agent start after Windows login.

## Install Background Startup

From PowerShell:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\install_startup_folder.ps1
```

This installs a Startup folder launcher so the background agent starts when you log in after reboot.

You can also enable or disable this directly in the UI with the `Start with Windows` checkbox.

## Remove Background Startup

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\uninstall_startup_folder.ps1
```

## Build One-File EXE

PyInstaller is used to package the app:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Output:

```text
dist\JobScheduler.exe
```

Run it normally to open the UI:

```powershell
.\dist\JobScheduler.exe
```

The same EXE can run the hidden background agent:

```powershell
.\dist\JobScheduler.exe --agent
```

## Schedule Options

- `Weekly`: choose one or more weekdays and a time, such as Monday/Wednesday/Friday at `09:00:30`.
- `Monthly`: choose a day of the month and time, such as day 5 at `18:30:15`.
- `Every`: run at a fixed interval in seconds, minutes, or hours.
- `Run once`: use `YYYY-MM-DD HH:MM:SS`; the job disables itself after it runs.
- `Schedule Time Zone`: weekly, monthly, run-once, and end-time fields are interpreted in the selected timezone.

## Features

- Choose a Python `.py` file or Windows `.exe` file.
- Add optional arguments.
- Add an optional end time so repeating jobs stop after a date/time.
- Select an IANA timezone for country/region-specific schedule times.
- Set a working directory; blank uses the selected file folder.
- Run now, enable, disable, edit, and delete jobs.
- Scrollbars and mouse-wheel scrolling are available for the job table, job details, logs, and job editor.
- Background agent keeps schedules running after the UI closes.
- Startup launcher can relaunch the background agent after reboot/login.
- Execution output is saved in `logs/`.
- Log files older than 31 days are automatically deleted by the background agent.
- Job settings are saved in `jobs.json`.

## Notes

This is designed to keep working after you close the UI and after reboot once you log in. Running before any user logs in requires a true Windows Service, which is a heavier setup.
