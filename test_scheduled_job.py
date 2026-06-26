from datetime import datetime
from pathlib import Path


output_file = Path(__file__).with_name("test_scheduled_job_output.txt")
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

with output_file.open("a", encoding="utf-8") as file:
    file.write(f"Test job ran at {timestamp}\n")

print(f"Test job completed at {timestamp}")
print(f"Wrote to: {output_file}")
