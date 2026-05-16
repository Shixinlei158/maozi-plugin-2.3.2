import subprocess, sys, os, time

os.chdir(r"C:\ozon_pipeline")
cmd = [sys.executable, "-m", "ozon_pipeline.cli", "expand-seed-pool-network",
       "--process-limit", "100", "--max-depth", "1", "--max-sellers", "20"]

print(f"Starting: {' '.join(cmd)}")
print(f"Time: {time.strftime('%H:%M:%S')}")
sys.stdout.flush()

p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

for line in p.stdout:
    print(line, end='')
    sys.stdout.flush()

p.wait()
print(f"\nExit: {p.returncode}, Time: {time.strftime('%H:%M:%S')}")
