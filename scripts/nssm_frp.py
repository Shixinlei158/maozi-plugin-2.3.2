import subprocess, os, sys

# Use nssm to install a proper Windows service wrapper
# First check if nssm exists, if not, download it
nssm_path = r"C:\project\ssh\nssm.exe"
if not os.path.exists(nssm_path):
    import urllib.request
    print("Downloading nssm...")
    url = "https://nssm.cc/release/nssm-2.24.zip"
    urllib.request.urlretrieve(url, r"C:\project\ssh\nssm.zip")
    import zipfile
    with zipfile.ZipFile(r"C:\project\ssh\nssm.zip") as z:
        for f in z.namelist():
            if "win64" in f and "nssm.exe" in f:
                z.extract(f, r"C:\project\ssh")
                os.rename(os.path.join(r"C:\project\ssh", f), nssm_path)
                print("nssm extracted")
                break

if os.path.exists(nssm_path):
    # Remove old service first
    subprocess.run('sc stop FRP_Client', capture_output=True, shell=True)
    subprocess.run('sc delete FRP_Client', capture_output=True, shell=True)
    
    # Install via nssm
    exe = r"C:\project\ssh\frp\frp_0.61.0_windows_amd64\frpc.exe"
    cfg = r"C:\project\ssh\frpc_windows.toml"
    
    cmd = f'"{nssm_path}" install FRP_Client "{exe}" -c "{cfg}"'
    print(f"Running: {cmd}")
    r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    print(r.stdout, r.stderr)
    
    # Set app directory
    cmd = f'"{nssm_path}" set FRP_Client AppDirectory C:\\project\\ssh'
    r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    
    # Set start type to auto
    cmd = f'"{nssm_path}" set FRP_Client Start SERVICE_AUTO_START'
    r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    
    # Start
    cmd = f'"{nssm_path}" start FRP_Client'
    r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    print("Start:", r.stdout, r.stderr)
    
    print("\nService installed via nssm. It should stay running now.")
else:
    print("nssm download failed, falling back to direct start")
    # Direct start with full detachment
    import time
    DETACHED_PROCESS = 0x00000008
    p = subprocess.Popen(
        [r"C:\project\ssh\frp\frp_0.61.0_windows_amd64\frpc.exe", "-c", r"C:\project\ssh\frpc_windows.toml"],
        creationflags=DETACHED_PROCESS,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    print(f"FRP started (PID {p.pid}), waiting 10 sec...")
    time.sleep(10)
    import os
    r = os.popen('tasklist').read()
    print("FRP running:", 'frpc' in r.lower())
