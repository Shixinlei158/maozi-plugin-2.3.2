import os, sys, time
sys.path.insert(0, "/home/lx/ozon_selection_pipeline")
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
ctx = p.chromium.launch_persistent_context(
    user_data_dir="/home/lx/ozon_selection_pipeline/profiles/server-profile",
    headless=True,
    executable_path="/home/lx/chrome/opt/google/chrome/chrome",
    args=[
        "--remote-debugging-port=9222",
        "--no-first-run", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-gpu", "--headless=new",
        "--disable-blink-features=AutomationControlled",
    ],
    viewport={"width": 1920, "height": 1080},
)
print("OK: Browser running on CDP 9222")
sys.stdout.flush()
while True:
    time.sleep(60)
