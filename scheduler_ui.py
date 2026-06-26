import calendar
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import (
    BOTH,
    DISABLED,
    END,
    LEFT,
    NORMAL,
    RIGHT,
    Y,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    LabelFrame,
    Listbox,
    Menu,
    Message,
    Radiobutton,
    Scrollbar,
    Spinbox,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)


APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "jobs.json"
LOG_DIR = APP_DIR / "logs"
AGENT_PID_FILE = APP_DIR / "scheduler_agent.pid"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
LEGACY_TIME_FORMAT = "%Y-%m-%d %H:%M"
CLOCK_FORMAT = "%H:%M"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
INTERVAL_UNITS = ["seconds", "minutes", "hours"]


@dataclass
class Job:
    name: str
    python_file: str
    python_args: str = ""
    file_type: str = "python"
    mode: str = "weekly"
    interval_minutes: int = 60
    interval_unit: str = "minutes"
    run_at: str = ""
    end_at: str = ""
    schedule_time: str = "09:00"
    weekday: int = 0
    month_day: int = 1
    working_dir: str = ""
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    next_run: str = ""
    last_run: str = ""
    last_status: str = "Not run yet"
    last_exit_code: int | None = None
    running: bool = False


class JobStore:
    def __init__(self, path: Path):
        self.path = path
        self.jobs: list[Job] = []

    def load(self) -> None:
        if not self.path.exists():
            self.jobs = []
            return

        with self.path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        self.jobs = []
        for item in payload:
            clean = self._migrate(item)
            self.jobs.append(Job(**clean))

    def save(self) -> None:
        with self.path.open("w", encoding="utf-8") as file:
            json.dump([asdict(job) for job in self.jobs], file, ensure_ascii=False, indent=2)

    def get(self, job_id: str) -> Job | None:
        return next((job for job in self.jobs if job.id == job_id), None)

    def upsert(self, job: Job) -> None:
        for index, existing in enumerate(self.jobs):
            if existing.id == job.id:
                self.jobs[index] = job
                return
        self.jobs.append(job)

    def remove(self, job_id: str) -> None:
        self.jobs = [job for job in self.jobs if job.id != job_id]

    def _migrate(self, item: dict) -> dict:
        known = set(Job.__dataclass_fields__.keys())
        clean = {key: value for key, value in item.items() if key in known}

        if "python_file" not in clean:
            command = item.get("command", "")
            clean["python_file"] = guess_python_file(command)
            clean["python_args"] = ""

        clean.setdefault("name", Path(clean.get("python_file") or "Scheduled Job").stem)
        clean.setdefault("file_type", detect_file_type(clean.get("python_file", "")))
        clean.setdefault("mode", item.get("mode", "weekly"))
        clean.setdefault("interval_minutes", item.get("interval_minutes", 60))
        clean.setdefault("interval_unit", item.get("interval_unit", "minutes"))
        clean.setdefault("run_at", item.get("run_at", ""))
        clean.setdefault("end_at", item.get("end_at", ""))
        clean.setdefault("schedule_time", "09:00")
        clean.setdefault("weekday", 0)
        clean.setdefault("month_day", 1)
        clean.setdefault("working_dir", item.get("working_dir", ""))
        clean.setdefault("enabled", True)
        clean.setdefault("last_status", "Not run yet")
        clean.setdefault("running", False)
        clean.setdefault("next_run", "")
        clean.setdefault("last_run", "")
        clean.setdefault("last_exit_code", None)
        clean.setdefault("id", uuid.uuid4().hex)
        return clean


class Scheduler:
    def __init__(self, store: JobStore, event_queue: queue.Queue):
        self.store = store
        self.event_queue = event_queue
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self.recalculate_missing_next_runs()
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def recalculate_missing_next_runs(self) -> None:
        changed = False
        now = datetime.now()
        with self.lock:
            for job in self.store.jobs:
                if job.enabled and is_job_past_end(job, now):
                    job.enabled = False
                    job.next_run = ""
                    job.last_status = "Ended"
                    changed = True
                elif job.enabled and not job.next_run:
                    job.next_run = format_time(calculate_next_run(job, now))
                    changed = True
        if changed:
            self.store.save()
            self.event_queue.put(("jobs_changed", None))

    def run_now(self, job_id: str) -> None:
        with self.lock:
            job = self.store.get(job_id)
            if not job or job.running:
                return
            self._start_job(job)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            now = datetime.now()
            with self.lock:
                for job in self.store.jobs:
                    if not job.enabled or job.running:
                        continue
                    if is_job_past_end(job, now):
                        job.enabled = False
                        job.next_run = ""
                        job.last_status = "Ended"
                        self.store.save()
                        self.event_queue.put(("jobs_changed", None))
                        continue
                    next_run = parse_datetime(job.next_run)
                    if next_run and next_run <= now:
                        self._start_job(job)
            self.stop_event.wait(1)

    def _start_job(self, job: Job) -> None:
        job.running = True
        job.last_status = "Running"
        job.last_run = format_time(datetime.now())
        self.store.save()
        self.event_queue.put(("jobs_changed", None))
        thread = threading.Thread(target=self._run_job, args=(job.id,), daemon=True)
        thread.start()

    def _run_job(self, job_id: str) -> None:
        started = datetime.now()
        with self.lock:
            job = self.store.get(job_id)
            if not job:
                return
            command = build_display_command(job)
            process_command = build_process_command(job)
            cwd = job.working_dir.strip() or str(Path(job.python_file).parent)
            name = safe_filename(job.name)

        LOG_DIR.mkdir(exist_ok=True)
        log_path = LOG_DIR / f"{started.strftime('%Y%m%d_%H%M%S')}_{name}.log"
        exit_code = -1
        status = "Failed"

        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"$ {command}\n\n")
            log_file.flush()
            try:
                if not job.python_file or not Path(job.python_file).exists():
                    raise RuntimeError("Program file was not found.")

                process = subprocess.Popen(
                    process_command,
                    cwd=cwd if cwd and os.path.isdir(cwd) else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                assert process.stdout is not None
                for line in process.stdout:
                    log_file.write(line)
                    log_file.flush()
                exit_code = process.wait()
                status = "Success" if exit_code == 0 else f"Failed ({exit_code})"
            except Exception as exc:
                log_file.write(f"\n[Scheduler error] {exc}\n")
                exit_code = -1
                status = "Failed"

        with self.lock:
            job = self.store.get(job_id)
            if job:
                job.running = False
                job.last_status = status
                job.last_exit_code = exit_code
                if job.mode == "once":
                    job.enabled = False
                    job.next_run = ""
                else:
                    next_run = calculate_next_run(job, datetime.now())
                    if is_job_past_end(job, next_run):
                        job.enabled = False
                        job.next_run = ""
                        job.last_status = "Ended"
                    else:
                        job.next_run = format_time(next_run)
                self.store.save()
        self.event_queue.put(("job_finished", str(log_path)))
        self.event_queue.put(("jobs_changed", None))


class JobDialog(Toplevel):
    def __init__(self, master: Tk, job: Job | None = None):
        super().__init__(master)
        self.title("Edit Job" if job else "New Job")
        self.geometry("680x560")
        self.minsize(560, 500)
        self.resizable(True, True)
        self.result: Job | None = None
        self.job = job

        self.name_var = StringVar(value=job.name if job else "")
        self.python_file_var = StringVar(value=job.python_file if job else "")
        self.file_type_var = StringVar(value=job.file_type if job else "python")
        self.python_args_var = StringVar(value=job.python_args if job else "")
        self.mode_var = StringVar(value=job.mode if job else "weekly")
        self.interval_var = StringVar(value=str(job.interval_minutes if job else 60))
        self.interval_unit_var = StringVar(value=job.interval_unit if job else "minutes")
        self.run_at_var = StringVar(value=job.run_at if job and job.run_at else datetime.now().strftime(TIME_FORMAT))
        self.end_at_var = StringVar(value=job.end_at if job else "")
        self.time_var = StringVar(value=job.schedule_time if job else "09:00")
        self.weekday_var = StringVar(value=WEEKDAYS[job.weekday] if job else WEEKDAYS[0])
        self.month_day_var = StringVar(value=str(job.month_day if job else 1))
        self.working_dir_var = StringVar(value=job.working_dir if job else "")
        self.enabled_var = BooleanVar(value=job.enabled if job else True)

        container = Frame(self, padx=14, pady=14)
        container.pack(fill=BOTH, expand=True)

        self._add_labeled_entry(container, "Name", self.name_var, 52)

        file_frame = Frame(container)
        file_frame.pack(fill=BOTH, pady=(0, 8))
        Label(file_frame, text="Python or EXE File").pack(anchor="w")
        Entry(file_frame, textvariable=self.python_file_var, width=45).pack(side=LEFT, fill=BOTH, expand=True, pady=(4, 0))
        Button(file_frame, text="Browse", command=self._choose_program_file).pack(side=RIGHT, padx=(8, 0), pady=(4, 0))

        type_frame = Frame(container)
        type_frame.pack(fill=BOTH, pady=(0, 8))
        Label(type_frame, text="Run As").pack(side=LEFT)
        Radiobutton(type_frame, text="Python", variable=self.file_type_var, value="python").pack(side=LEFT, padx=(12, 0))
        Radiobutton(type_frame, text="EXE", variable=self.file_type_var, value="exe").pack(side=LEFT, padx=(8, 0))

        self._add_labeled_entry(container, "Arguments (optional)", self.python_args_var, 52)

        schedule_box = LabelFrame(container, text="Schedule", padx=10, pady=8)
        schedule_box.pack(fill=BOTH, pady=(8, 0))
        schedule_box.columnconfigure(3, weight=1)

        Radiobutton(schedule_box, text="Weekly", variable=self.mode_var, value="weekly").grid(row=0, column=0, sticky="w")
        ttk.Combobox(schedule_box, textvariable=self.weekday_var, values=WEEKDAYS, width=8, state="readonly").grid(
            row=0, column=1, padx=(8, 6), sticky="w"
        )
        Label(schedule_box, text="Time").grid(row=0, column=2, sticky="w")
        Entry(schedule_box, textvariable=self.time_var, width=8).grid(row=0, column=3, padx=(6, 0), sticky="w")

        Radiobutton(schedule_box, text="Monthly", variable=self.mode_var, value="monthly").grid(row=1, column=0, sticky="w", pady=(8, 0))
        Spinbox(schedule_box, from_=1, to=31, textvariable=self.month_day_var, width=6).grid(
            row=1, column=1, padx=(8, 6), sticky="w", pady=(8, 0)
        )
        Label(schedule_box, text="day, at").grid(row=1, column=2, sticky="w", pady=(8, 0))
        Entry(schedule_box, textvariable=self.time_var, width=8).grid(row=1, column=3, padx=(6, 0), sticky="w", pady=(8, 0))

        Radiobutton(schedule_box, text="Every", variable=self.mode_var, value="interval").grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )
        Entry(schedule_box, textvariable=self.interval_var, width=8).grid(row=2, column=1, padx=(8, 6), sticky="w", pady=(8, 0))
        ttk.Combobox(schedule_box, textvariable=self.interval_unit_var, values=INTERVAL_UNITS, width=8, state="readonly").grid(
            row=2, column=2, sticky="w", pady=(8, 0)
        )

        Radiobutton(schedule_box, text="Run once", variable=self.mode_var, value="once").grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )
        Entry(schedule_box, textvariable=self.run_at_var, width=18).grid(row=3, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0))
        Label(schedule_box, text="Time format: HH:MM; one-time format: YYYY-MM-DD HH:MM:SS").grid(
            row=4, column=1, columnspan=3, sticky="w", pady=(4, 0)
        )

        end_frame = Frame(container)
        end_frame.pack(fill=BOTH, pady=(10, 0))
        Label(end_frame, text="End Time (optional, YYYY-MM-DD HH:MM:SS)").pack(anchor="w")
        Entry(end_frame, textvariable=self.end_at_var, width=45).pack(fill=BOTH, expand=True, pady=(4, 0))

        path_frame = Frame(container)
        path_frame.pack(fill=BOTH, pady=(10, 0))
        Label(path_frame, text="Working Directory (blank uses the selected file folder)").pack(anchor="w")
        Entry(path_frame, textvariable=self.working_dir_var, width=45).pack(side=LEFT, fill=BOTH, expand=True, pady=(4, 0))
        Button(path_frame, text="Browse", command=self._choose_dir).pack(side=RIGHT, padx=(8, 0), pady=(4, 0))

        Checkbutton(container, text="Enable this job", variable=self.enabled_var).pack(anchor="w", pady=(10, 0))

        buttons = Frame(container)
        buttons.pack(fill=BOTH, pady=(14, 0))
        Button(buttons, text="Cancel", command=self.destroy).pack(side=RIGHT)
        Button(buttons, text="Save", command=self._save).pack(side=RIGHT, padx=(0, 8))

        self.transient(master)
        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _add_labeled_entry(self, parent: Frame, label: str, variable: StringVar, width: int) -> None:
        Label(parent, text=label).pack(anchor="w")
        Entry(parent, textvariable=variable, width=width).pack(fill=BOTH, pady=(4, 8))

    def _choose_program_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Choose Python or EXE File",
            filetypes=[("Python or EXE files", "*.py *.exe"), ("Python files", "*.py"), ("EXE files", "*.exe"), ("All files", "*.*")],
        )
        if selected:
            self.python_file_var.set(selected)
            self.file_type_var.set(detect_file_type(selected))
            if not self.name_var.get().strip():
                self.name_var.set(Path(selected).stem)
            if not self.working_dir_var.get().strip():
                self.working_dir_var.set(str(Path(selected).parent))

    def _choose_dir(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Choose Working Directory")
        if selected:
            self.working_dir_var.set(selected)

    def _save(self) -> None:
        name = self.name_var.get().strip()
        python_file = self.python_file_var.get().strip()
        if not name or not python_file:
            messagebox.showerror("Missing Information", "Enter a name and choose a Python or EXE file.", parent=self)
            return
        if not Path(python_file).exists():
            messagebox.showerror("File Not Found", "The selected file does not exist.", parent=self)
            return

        try:
            interval = int(self.interval_var.get().strip())
            if interval < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Schedule Error", "Interval must be an integer greater than 0.", parent=self)
            return

        mode = self.mode_var.get()
        if mode == "once" and not parse_datetime(self.run_at_var.get().strip()):
            messagebox.showerror("Invalid Time Format", f"Use the {TIME_FORMAT} format.", parent=self)
            return
        if mode in {"weekly", "monthly"} and not parse_clock(self.time_var.get().strip()):
            messagebox.showerror("Invalid Time Format", "Weekly and monthly schedules must use HH:MM.", parent=self)
            return
        end_at = self.end_at_var.get().strip()
        if end_at and not parse_datetime(end_at):
            messagebox.showerror("Invalid End Time", f"End time must use {TIME_FORMAT}.", parent=self)
            return

        try:
            month_day = int(self.month_day_var.get().strip())
            if month_day < 1 or month_day > 31:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Date", "Monthly day must be between 1 and 31.", parent=self)
            return

        job_id = self.job.id if self.job else uuid.uuid4().hex
        job = Job(
            id=job_id,
            name=name,
            python_file=python_file,
            python_args=self.python_args_var.get().strip(),
            file_type=self.file_type_var.get(),
            mode=mode,
            interval_minutes=interval,
            interval_unit=self.interval_unit_var.get(),
            run_at=self.run_at_var.get().strip(),
            end_at=end_at,
            schedule_time=self.time_var.get().strip(),
            weekday=WEEKDAYS.index(self.weekday_var.get()),
            month_day=month_day,
            working_dir=self.working_dir_var.get().strip(),
            enabled=self.enabled_var.get(),
            next_run="",
            last_run=self.job.last_run if self.job else "",
            last_status=self.job.last_status if self.job else "Not run yet",
            last_exit_code=self.job.last_exit_code if self.job else None,
            running=self.job.running if self.job else False,
        )
        next_run = calculate_next_run(job, datetime.now()) if job.enabled else None
        job.next_run = format_time(next_run) if next_run and not is_job_past_end(job, next_run) else ""
        if job.enabled and not job.next_run:
            job.enabled = False
            job.last_status = "Ended"
        self.result = job
        self.destroy()


class SchedulerApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Python / EXE Job Scheduler")
        self.root.geometry("1060x640")
        self.root.minsize(760, 480)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        self.events: queue.Queue = queue.Queue()
        self.store = JobStore(DATA_FILE)
        self.store.load()
        self.scheduler = Scheduler(self.store, self.events)

        self.selected_job_id: str | None = None
        self.status_var = StringVar(value=self._agent_status_text())

        self._build_ui()
        self._refresh_jobs()
        self._start_agent_if_needed()
        self._poll_events()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        menubar = Menu(self.root)
        file_menu = Menu(menubar, tearoff=False)
        file_menu.add_command(label="Open Logs Folder", command=self._open_logs_dir)
        file_menu.add_command(label="Start Background Agent", command=self._start_agent_clicked)
        file_menu.add_command(label="Install Startup Task", command=self._install_startup_task)
        file_menu.add_command(label="Reload", command=self._reload)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menubar)

        header = Frame(self.root, padx=14, pady=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        Label(header, text="Python / EXE Job Scheduler", font=("Segoe UI", 18, "bold")).pack(side=LEFT)
        Message(header, textvariable=self.status_var, width=560).pack(side=RIGHT)

        toolbar = Frame(self.root, padx=14)
        toolbar.grid(row=1, column=0, sticky="ew")
        Button(toolbar, text="New", command=self._add_job).pack(side=LEFT)
        Button(toolbar, text="Edit", command=self._edit_job).pack(side=LEFT, padx=(8, 0))
        Button(toolbar, text="Delete", command=self._delete_job).pack(side=LEFT, padx=(8, 0))
        Button(toolbar, text="Run Now", command=self._run_selected_now).pack(side=LEFT, padx=(18, 0))
        Button(toolbar, text="Enable / Disable", command=self._toggle_selected).pack(side=LEFT, padx=(8, 0))

        body = Frame(self.root, padx=14, pady=12)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(body, orient="horizontal")
        panes.grid(row=0, column=0, sticky="nsew")

        left = Frame(panes)
        right = Frame(panes)
        panes.add(left, weight=4)
        panes.add(right, weight=2)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=3)
        right.rowconfigure(3, weight=2)

        columns = ("enabled", "name", "file", "schedule", "next", "last", "status")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", height=16)
        headings = {
            "enabled": "State",
            "name": "Name",
            "file": "File",
            "schedule": "Schedule",
            "next": "Next Run",
            "last": "Last Run",
            "status": "Result",
        }
        widths = {"enabled": 70, "name": 140, "file": 190, "schedule": 150, "next": 140, "last": 140, "status": 110}
        self.tree_column_weights = {"enabled": 7, "name": 15, "file": 23, "schedule": 16, "next": 15, "last": 15, "status": 9}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=60, anchor="w", stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _event: self._edit_job())
        self.tree.bind("<Configure>", self._resize_tree_columns)

        scroll = Scrollbar(left, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        Label(right, text="Job Details", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        self.detail = Text(right, height=16, wrap="word", state=DISABLED)
        self.detail.grid(row=1, column=0, sticky="nsew", pady=(8, 12))
        Label(right, text="Recent Logs", font=("Segoe UI", 12, "bold")).grid(row=2, column=0, sticky="w")
        self.log_list = Listbox(right, height=8)
        self.log_list.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        Button(right, text="Open Selected Log", command=self._open_selected_log).grid(row=4, column=0, sticky="e", pady=(8, 0))

    def _resize_tree_columns(self, _event=None) -> None:
        available_width = max(self.tree.winfo_width() - 24, 480)
        total_weight = sum(self.tree_column_weights.values())
        for column, weight in self.tree_column_weights.items():
            width = int(available_width * weight / total_weight)
            min_width = 70 if column in {"enabled", "status"} else 90
            self.tree.column(column, width=max(min_width, width))

    def _refresh_jobs(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        for job in sorted(self.store.jobs, key=lambda item: item.name.lower()):
            state = "Running" if job.running else ("Enabled" if job.enabled else "Disabled")
            self.tree.insert(
                "",
                END,
                iid=job.id,
                values=(
                    state,
                    job.name,
                    Path(job.python_file).name,
                    describe_schedule(job),
                    job.next_run or "-",
                    job.last_run or "-",
                    job.last_status,
                ),
            )

        if self.selected_job_id and self.selected_job_id in self.tree.get_children():
            self.tree.selection_set(self.selected_job_id)
        self._refresh_logs()
        self._show_detail()

    def _refresh_logs(self) -> None:
        self.log_list.delete(0, END)
        LOG_DIR.mkdir(exist_ok=True)
        logs = sorted(LOG_DIR.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
        for log in logs[:30]:
            self.log_list.insert(END, log.name)

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self.selected_job_id = selection[0] if selection else None
        self._show_detail()

    def _show_detail(self) -> None:
        self.detail.configure(state=NORMAL)
        self.detail.delete("1.0", END)
        job = self.store.get(self.selected_job_id) if self.selected_job_id else None
        if not job:
            self.detail.insert(END, "Select a job.")
        else:
            lines = [
                f"Name: {job.name}",
                f"State: {'Running' if job.running else ('Enabled' if job.enabled else 'Disabled')}",
                f"File: {job.python_file}",
                f"Run As: {job.file_type.upper()}",
                f"Arguments: {job.python_args or '(none)'}",
                f"Command: {build_display_command(job)}",
                f"Schedule: {describe_schedule(job)}",
                f"End Time: {job.end_at or '(none)'}",
                f"Working Directory: {job.working_dir or str(Path(job.python_file).parent)}",
                f"Next Run: {job.next_run or '-'}",
                f"Last Run: {job.last_run or '-'}",
                f"Result: {job.last_status}",
            ]
            self.detail.insert(END, "\n".join(lines))
        self.detail.configure(state=DISABLED)

    def _add_job(self) -> None:
        dialog = JobDialog(self.root)
        self.root.wait_window(dialog)
        if dialog.result:
            self.store.upsert(dialog.result)
            self.store.save()
            self._refresh_jobs()

    def _edit_job(self) -> None:
        job = self._selected_job()
        if not job:
            return
        dialog = JobDialog(self.root, job)
        self.root.wait_window(dialog)
        if dialog.result:
            self.store.upsert(dialog.result)
            self.store.save()
            self._refresh_jobs()

    def _delete_job(self) -> None:
        job = self._selected_job()
        if not job:
            return
        if messagebox.askyesno("Delete Job", f"Delete \"{job.name}\"?"):
            self.store.remove(job.id)
            self.store.save()
            self.selected_job_id = None
            self._refresh_jobs()

    def _run_selected_now(self) -> None:
        job = self._selected_job()
        if job:
            self.scheduler.run_now(job.id)

    def _toggle_selected(self) -> None:
        job = self._selected_job()
        if not job:
            return
        job.enabled = not job.enabled
        next_run = calculate_next_run(job, datetime.now()) if job.enabled else None
        job.next_run = format_time(next_run) if next_run and not is_job_past_end(job, next_run) else ""
        if job.enabled and not job.next_run:
            job.enabled = False
            job.last_status = "Ended"
        self.store.save()
        self._refresh_jobs()

    def _selected_job(self) -> Job | None:
        if not self.selected_job_id:
            messagebox.showinfo("No Selection", "Select a job first.")
            return None
        job = self.store.get(self.selected_job_id)
        if not job:
            messagebox.showinfo("Job Not Found", "This job no longer exists. Select another job.")
        return job

    def _reload(self) -> None:
        self.store.load()
        self.scheduler.recalculate_missing_next_runs()
        self._refresh_jobs()

    def _open_logs_dir(self) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        os.startfile(LOG_DIR)

    def _open_selected_log(self) -> None:
        selection = self.log_list.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Select a log first.")
            return
        path = LOG_DIR / self.log_list.get(selection[0])
        if path.exists():
            os.startfile(path)

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "jobs_changed":
                    self._refresh_jobs()
                elif event == "job_finished":
                    self.status_var.set(f"Job finished. Log: {payload}")
                    self._refresh_jobs()
        except queue.Empty:
            pass
        self.root.after(500, self._poll_events)

    def _agent_status_text(self) -> str:
        pid = read_agent_pid()
        if pid and is_pid_running(pid):
            return f"Background agent is running (PID {pid})."
        return "Background agent is not running."

    def _start_agent_clicked(self) -> None:
        if start_background_agent():
            self.status_var.set(self._agent_status_text())
            messagebox.showinfo("Background Agent", "Background agent started.")
        else:
            messagebox.showinfo("Background Agent", "Background agent is already running.")

    def _start_agent_if_needed(self) -> None:
        start_background_agent()
        self.status_var.set(self._agent_status_text())

    def _install_startup_task(self) -> None:
        script = APP_DIR / "install_scheduler_startup.ps1"
        result = subprocess.run(
            [
                "powershell.exe",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=str(APP_DIR),
            text=True,
            capture_output=True,
        )
        if result.returncode == 0:
            messagebox.showinfo("Startup Task", "Startup task installed. The background agent will start when you log in.")
        else:
            messagebox.showerror("Startup Task", result.stderr or result.stdout or "Failed to install startup task.")

    def _on_close(self) -> None:
        self.scheduler.stop()
        self.root.destroy()


def detect_file_type(path: str) -> str:
    return "exe" if Path(path).suffix.lower() == ".exe" else "python"


def build_process_command(job: Job) -> list[str]:
    args = shlex.split(job.python_args, posix=False) if job.python_args else []
    if job.file_type == "exe":
        return [job.python_file, *args]
    return [python_console_executable(), job.python_file, *args]


def build_display_command(job: Job) -> str:
    parts = [python_console_executable() if job.file_type == "python" else job.python_file]
    if job.file_type == "python":
        parts.append(job.python_file)
    if job.python_args:
        parts.extend(shlex.split(job.python_args, posix=False))
    return subprocess.list2cmdline(parts)


def python_console_executable() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        python_exe = executable.with_name("python.exe")
        if python_exe.exists():
            return str(python_exe)
    return sys.executable


def read_agent_pid() -> int | None:
    try:
        return int(AGENT_PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", f"Get-Process -Id {pid} -ErrorAction SilentlyContinue"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def start_background_agent() -> bool:
    pid = read_agent_pid()
    if pid and is_pid_running(pid):
        return False

    agent_path = APP_DIR / "scheduler_agent.py"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = str(pythonw if pythonw.exists() else sys.executable)
    subprocess.Popen(
        [launcher, str(agent_path)],
        cwd=str(APP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
    )
    return True


def calculate_next_run(job: Job, now: datetime) -> datetime:
    if job.mode == "once":
        return parse_datetime(job.run_at) or now
    if job.mode == "interval":
        interval = build_interval_delta(job)
        candidate = now + interval
        return candidate.replace(microsecond=0)
    if job.mode == "weekly":
        return next_weekly_run(job.weekday, job.schedule_time, now)
    if job.mode == "monthly":
        return next_monthly_run(job.month_day, job.schedule_time, now)
    return now


def next_weekly_run(weekday: int, clock_text: str, now: datetime) -> datetime:
    hour, minute = parse_clock(clock_text) or (9, 0)
    days_ahead = (weekday - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def next_monthly_run(month_day: int, clock_text: str, now: datetime) -> datetime:
    hour, minute = parse_clock(clock_text) or (9, 0)
    year = now.year
    month = now.month
    for _ in range(24):
        last_day = calendar.monthrange(year, month)[1]
        if month_day <= last_day:
            candidate = datetime(year, month, month_day, hour, minute)
            if candidate > now:
                return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1
    return now + timedelta(days=31)


def describe_schedule(job: Job) -> str:
    if job.mode == "weekly":
        return f"Weekly on {WEEKDAYS[job.weekday]} at {job.schedule_time}"
    if job.mode == "monthly":
        return f"Monthly on day {job.month_day} at {job.schedule_time}"
    if job.mode == "interval":
        return f"Every {job.interval_minutes} {job.interval_unit}"
    if job.mode == "once":
        return f"Once: {job.run_at}"
    return job.mode


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    for time_format in (TIME_FORMAT, LEGACY_TIME_FORMAT):
        try:
            return datetime.strptime(value, time_format)
        except ValueError:
            continue
    return None


def is_job_past_end(job: Job, when: datetime | None = None) -> bool:
    end_at = parse_datetime(job.end_at)
    if not end_at:
        return False
    return (when or datetime.now()) > end_at


def parse_clock(value: str) -> tuple[int, int] | None:
    try:
        parsed = datetime.strptime(value, CLOCK_FORMAT)
        return parsed.hour, parsed.minute
    except ValueError:
        return None


def format_time(value: datetime) -> str:
    return value.strftime(TIME_FORMAT)


def build_interval_delta(job: Job) -> timedelta:
    amount = max(1, int(job.interval_minutes))
    unit = job.interval_unit if job.interval_unit in INTERVAL_UNITS else "minutes"
    if unit == "seconds":
        return timedelta(seconds=amount)
    if unit == "hours":
        return timedelta(hours=amount)
    return timedelta(minutes=amount)


def guess_python_file(command: str) -> str:
    for token in command.replace('"', " ").replace("'", " ").split():
        if token.endswith(".py") and Path(token).exists():
            return token
    return ""


def safe_filename(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)
    return safe[:60] or "job"


def main() -> None:
    root = Tk()
    SchedulerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
