import subprocess, sys
# Create a scheduled task to run FRP, then start it immediately
task_name = "FRP Client Laptop"
frp_cmd = r"C:\project\ssh\frp\frp_0.61.0_windows_amd64\frpc.exe"
frp_arg = r"-c C:\project\ssh\frpc_windows.toml"

# Create task
create_cmd = f'schtasks /create /tn "{task_name}" /tr "\\"{frp_cmd}\\" {frp_arg}" /sc onlogon /f /rl highest'
print(f"Creating: {create_cmd}")
r1 = subprocess.run(create_cmd, capture_output=True, text=True, shell=True)
print("Create:", r1.stdout.strip(), r1.stderr.strip())

# Run task immediately  
run_cmd = f'schtasks /run /tn "{task_name}"'
print(f"Running: {run_cmd}")
r2 = subprocess.run(run_cmd, capture_output=True, text=True, shell=True)
print("Run:", r2.stdout.strip(), r2.stderr.strip())

# Check result
q_cmd = f'schtasks /query /tn "{task_name}" /fo LIST 2>&1'
r3 = subprocess.run(q_cmd, capture_output=True, text=True, shell=True)
print("Status:\n", r3.stdout[:500])
