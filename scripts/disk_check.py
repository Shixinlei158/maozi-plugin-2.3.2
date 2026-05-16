import subprocess, os

print("=== DISK LAYOUT ===")
r = subprocess.run('wmic logicaldisk get deviceid,size,freespace', capture_output=True, text=True, shell=True)
for line in r.stdout.splitlines():
    line = line.strip()
    if line and 'DeviceID' not in line:
        nums = line.split()
        if len(nums) >= 3:
            drive = nums[0]
            try:
                free_gb = int(nums[1]) / (1024**3)
                size_gb = int(nums[2]) / (1024**3)
                print(f"  {drive}: {size_gb:.0f}GB total, {free_gb:.0f}GB free")
            except:
                pass

print("\n=== D盘内容 ===")
for item in os.listdir('D:\\'):
    path = os.path.join('D:\\', item)
    try:
        size = sum(os.path.getsize(os.path.join(dp, f)) for dp, dn, fn in os.walk(path) for f in fn) / (1024**3)
        print(f"  {'[DIR]' if os.path.isdir(path) else '[FILE]'} {item} ({size:.1f}GB)")
    except:
        print(f"  {'[DIR]' if os.path.isdir(path) else '[FILE]'} {item}")

print("\n=== C盘大文件夹 ===")
ignore = ['Windows', 'Program Files', 'Program Files (x86)', 'ProgramData', 'Users']
for item in os.listdir('C:\\'):
    if item in ignore: continue
    path = os.path.join('C:\\', item)
    try:
        size = sum(os.path.getsize(os.path.join(dp, f)) for dp, dn, fn in os.walk(path) for f in fn) / (1024**3)
        if size > 0.1:
            print(f"  {'[DIR]' if os.path.isdir(path) else '[FILE]'} {item} ({size:.1f}GB)")
    except:
        pass

print("\n=== 已安装程序 ===")
r = subprocess.run('wmic product get name,version', capture_output=True, text=True, shell=True)
for line in r.stdout.splitlines():
    line = line.strip()
    if line and 'Name' not in line and line:
        print(f"  {line}")
