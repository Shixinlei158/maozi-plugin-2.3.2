"""lx 完整采集启动器：启动浏览器 + 榜单采集"""
import os, sys, time, threading
sys.path.insert(0, "/home/lx/ozon_selection_pipeline")

# 1. 启动浏览器
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
browser = p.chromium.launch(
    headless=True,
    executable_path="/home/lx/chrome/opt/google/chrome/chrome",
    args=[
        "--remote-debugging-port=9222",
        "--no-first-run", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-gpu", "--headless=new",
        "--disable-extensions", "--disable-blink-features=AutomationControlled",
        "--window-size=1920,1080",
    ],
)
print("Browser started on CDP 9222")

# 2. 跑采集
time.sleep(3)
import subprocess
env = os.environ.copy()
env["PYTHONPATH"] = "."
env["MAOZI_USERNAME"] = "15888913427"
env["MAOZI_PASSWORD"] = ".NVc5cS5x3n8xBQ"
result = subprocess.run([
    "python3", "-m", "ozon_pipeline.cli", "crawl-top-list-network",
    "--cdp-url", "http://127.0.0.1:9222",
    "--main-type", "hot",
    "--page-from", "1", "--page-to", "10",
    "--process-limit", "0", "--max-depth", "-1",
    "--retry-rejected-now",
], cwd="/home/lx/ozon_selection_pipeline", env=env)

print(f"Collection exit code: {result.returncode}")
browser.close()
p.stop()
