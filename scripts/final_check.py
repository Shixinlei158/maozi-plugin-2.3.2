import os, subprocess
# Clean temp Python scripts
for f in os.listdir(os.path.expanduser('~')):
    if f.endswith('.py') and os.path.isfile(os.path.expanduser(f'~/{f}')):
        try: os.remove(os.path.expanduser(f'~/{f}')); print(f'  Del: {f}')
        except: pass

# Verify scheduled tasks
print('\n=== 计划任务 ===')
r = subprocess.run('schtasks /query /fo LIST', capture_output=True, text=True, shell=True)
for l in r.stdout.splitlines():
    if 'TaskName' in l: print(l.strip())

# Verify FRP running
print('\n=== FRP ===')
r = subprocess.run('schtasks /query /tn FRP_AutoStart /fo LIST', capture_output=True, text=True, shell=True)
for l in r.stdout.splitlines():
    if any(k in l.lower() for k in ['status','taskname','schedule']): print(l.strip())

# Verify pipeline files
print('\n=== 采集项目 ===')
for f in os.listdir('C:\\ozon_pipeline'):
    print(f'  {f}')
for f in os.listdir('C:\\ozon_pipeline\\ozon_pipeline'):
    print(f'  ozon_pipeline/{f}')

# Memory
r = subprocess.run('wmic OS get FreePhysicalMemory /Value', capture_output=True, text=True, shell=True)
for l in r.stdout.splitlines():
    if 'FreePhysicalMemory' in l: print(f'\n内存: {int(l.split("=")[-1].strip())//1024}MB / 12GB')
