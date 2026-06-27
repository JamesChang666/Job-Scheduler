import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from scheduler_ui import (
    AGENT_PID_FILE,
    DATA_FILE,
    LOG_DIR,
    Job,
    JobStore,
    build_display_command,
    build_job_environment,
    build_process_command,
    calculate_next_run,
    format_time,
    is_job_past_end,
    parse_datetime,
    safe_filename,
)


POLL_SECONDS = 1
LOG_RETENTION_DAYS = 31
LOG_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60


class BackgroundScheduler:
    def __init__(self):
        self.active_jobs: set[str] = set()
        self.lock = threading.Lock()
        self.last_log_cleanup = 0.0

    def run_forever(self) -> None:
        AGENT_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        try:
            while True:
                self.tick()
                self.cleanup_old_logs()
                time.sleep(POLL_SECONDS)
        finally:
            try:
                AGENT_PID_FILE.unlink()
            except OSError:
                pass

    def tick(self) -> None:
        store = JobStore(DATA_FILE)
        store.load()
        now = datetime.now()
        changed = False
        jobs_to_start: list[str] = []

        for job in store.jobs:
            if not job.enabled:
                continue

            if is_job_past_end(job, now):
                job.enabled = False
                job.next_run = ""
                job.last_status = "Ended"
                changed = True
                continue

            if not job.next_run:
                next_run = calculate_next_run(job, now)
                if is_job_past_end(job, next_run):
                    job.enabled = False
                    job.next_run = ""
                    job.last_status = "Ended"
                else:
                    job.next_run = format_time(next_run)
                changed = True
                continue

            next_run = parse_datetime(job.next_run)
            if next_run and next_run <= now and job.id not in self.active_jobs:
                self.active_jobs.add(job.id)
                job.running = True
                job.last_status = "Running"
                job.last_run = format_time(now)
                changed = True
                jobs_to_start.append(job.id)

        if changed:
            store.save()

        for job_id in jobs_to_start:
            threading.Thread(target=self.run_job, args=(job_id,), daemon=True).start()

    def run_job(self, job_id: str) -> None:
        store = JobStore(DATA_FILE)
        store.load()
        job = store.get(job_id)
        if not job:
            self.active_jobs.discard(job_id)
            return

        started = datetime.now()
        command = build_display_command(job)
        process_command = build_process_command(job)
        cwd = job.working_dir.strip() or str(Path(job.python_file).parent)
        log_path = self.log_path(job, started)
        exit_code = -1
        status = "Failed"

        LOG_DIR.mkdir(exist_ok=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"$ {command}\n\n")
            log_file.flush()
            try:
                if not job.python_file or not Path(job.python_file).exists():
                    raise RuntimeError("Program file was not found.")

                process = subprocess.Popen(
                    process_command,
                    cwd=cwd if cwd and os.path.isdir(cwd) else None,
                    env=build_job_environment(job),
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

        store.load()
        job = store.get(job_id)
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
            store.save()

        self.active_jobs.discard(job_id)

    def log_path(self, job: Job, started: datetime) -> Path:
        name = safe_filename(job.name)
        return LOG_DIR / f"{started.strftime('%Y%m%d_%H%M%S')}_{name}.log"

    def cleanup_old_logs(self) -> None:
        now = time.time()
        if now - self.last_log_cleanup < LOG_CLEANUP_INTERVAL_SECONDS:
            return

        self.last_log_cleanup = now
        cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
        if not LOG_DIR.exists():
            return

        for log_file in LOG_DIR.glob("*.log"):
            try:
                modified = datetime.fromtimestamp(log_file.stat().st_mtime)
                if modified < cutoff:
                    log_file.unlink()
            except OSError:
                continue


def main() -> None:
    BackgroundScheduler().run_forever()


if __name__ == "__main__":
    main()
