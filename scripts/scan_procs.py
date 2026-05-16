import subprocess, os

print("=== 运行中进程（非系统核心）===")
r = os.popen('tasklist /FO CSV /NH').read()
lines = r.splitlines()
system_procs = {
    'svchost.exe', 'csrss.exe', 'winlogon.exe', 'services.exe', 'lsass.exe',
    'smss.exe', 'wininit.exe', 'System', 'Idle', 'Registry', 'Memory Compression',
    'audiodg.exe', 'fontdrvhost.exe', 'dwm.exe', 'spoolsv.exe',
    'sppsvc.exe', 'wlms.exe', 'WmiPrvSE.exe', 'conhost.exe', 'cmd.exe',
    'sshd.exe', 'tasklist.exe', 'taskmgr.exe',
}
essential = {'SunloginClient.exe', 'frpc.exe', 'explorer.exe', 'python.exe', 'python3.exe'}

others = []
for line in lines:
    parts = line.replace('"', '').split(',')
    if len(parts) >= 5:
        name = parts[0].strip()
        pid = parts[1].strip()
        mem_str = parts[4].strip().replace(' K', '').replace(',', '')
        try: mem_kb = int(mem_str)
        except: mem_kb = 0
        if name.lower() not in {p.lower() for p in system_procs} and name.lower() not in {p.lower() for p in essential}:
            others.append((name, pid, mem_kb))

others.sort(key=lambda x: -x[2])
print(f"{'进程':<45} {'PID':<8} {'内存(KB)'}")
for name, pid, mem in others:
    print(f"{name:<45} {pid:<8} {mem}")

print("\n=== Edge/Chrome 后台进程 ===")
r = os.popen('tasklist /FI "IMAGENAME eq msedge.exe" /FO CSV /NH').read()
for l in r.splitlines():
    if l.strip():
        parts = l.replace('"', '').split(',')
        print(f"  {parts[0]}: PID {parts[1]}")
r = os.popen('tasklist /FI "IMAGENAME eq chrome.exe" /FO CSV /NH').read()
for l in r.splitlines():
    if l.strip():
        parts = l.replace('"', '').split(',')
        print(f"  {parts[0]}: PID {parts[1]}")
