import os
r = os.popen('tasklist').read()
print('frpc.exe running:', 'frpc.exe' in r)
# Also check via wmic
import subprocess
r = subprocess.run('wmic process where name="frpc.exe" get ProcessId', capture_output=True, text=True, shell=True)
print('WMIC:', r.stdout.strip())
