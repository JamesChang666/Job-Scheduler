from datetime import datetime

path = r"C:\Users\james\Desktop\工作排程器\test_scheduled_job_output.txt"

with open(path, "a", encoding="utf-8") as f:
    f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
