import subprocess, os

print("=== FRP进程 ===")
r = os.popen('tasklist /FI "IMAGENAME eq frpc.exe"').read()
print(r)

print("\n=== FRP端口 ===")
r = os.popen('netstat -ano | findstr ":6002"').read()
print(r.strip() or "6002 not found locally")

print("\n=== TOP20 内存占用进程 ===")
r = os.popen('tasklist /FO CSV /NH').read()
procs = []
for line in r.splitlines():
    parts = line.replace('"', '').split(',')
    if len(parts) >= 5:
        name = parts[0].strip()
        pid = parts[1].strip()
        mem_str = parts[4].strip().replace(' K', '').replace(',', '')
        try:
            mem_kb = int(mem_str)
            procs.append((name, pid, mem_kb))
        except:
            pass

procs.sort(key=lambda x: -x[2])
print(f"{'进程':<30} {'PID':<8} {'内存(MB)':<10}")
for name, pid, mem in procs[:20]:
    print(f"{name:<30} {pid:<8} {mem//1024:<10}")

print("\n=== 总内存 ===")
r = os.popen('wmic OS get TotalVisibleMemorySize,FreePhysicalMemory /Value').read()
print(r.strip())

print("\n=== 可安全关闭的进程 ===")
safe_to_kill = [
    'wps.exe', 'WPSOffice.exe', 'cloudmusic.exe', 'baidunetdisk.exe',
    'thunder.exe', 'QQ.exe', 'WeChat.exe', 'dingtalk.exe',
    '360se.exe', 'sogouexplorer.exe', 'QQBrowser.exe',
    'OneDrive.exe', 'MicrosoftEdge.exe', 'msedge.exe',
    'RuntimeBroker.exe', 'Widgets.exe', 'SearchApp.exe',
    'YourPhone.exe', 'Skype.exe', 'Teams.exe',
    'Adobe*.exe', 'CCleaner*.exe', 'NVIDIA Share.exe',
    'nvcontainer.exe', 'RadeonSoftware.exe',
    'jusched.exe', 'scheduler_proxy.exe',
]

for name_pattern in safe_to_kill:
    r = os.popen(f'tasklist /FI "IMAGENAME eq {name_pattern}"').read()
    for line in r.splitlines():
        if '.exe' in line.lower():
            parts = line.split()
            if len(parts) >= 2:
                print(f"  发现: {line[:80]}...")
