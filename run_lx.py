#!/usr/bin/env python3
"""lx一体化采集：启动Playwright Chrome + 榜单采集"""
import os, sys, time, subprocess
sys.path.insert(0, "/home/lx/ozon_selection_pipeline")

# 确保环境变量
os.environ["MAOZI_USERNAME"] = "15888913427"
os.environ["MAOZI_PASSWORD"] = ".NVc5cS5x3n8xBQ"
os.environ["PYTHONPATH"] = "."

from playwright.sync_api import sync_playwright

print("Starting Chrome...")
p = sync_playwright().start()
b = p.chromium.launch(
    headless=True,
    executable_path="/home/lx/chrome/opt/google/chrome/chrome",
    args=[
        "--remote-debugging-port=9222",
        "--no-first-run", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-gpu", "--headless=new",
        "--disable-extensions", "--disable-blink-features=AutomationControlled",
    ],
)
print("Chrome started on CDP 9222")
sys.stdout.flush()
time.sleep(3)

print("Starting collection...")
print(f"DEBUG: MAOZI_USERNAME in env: {os.environ.get('MAOZI_USERNAME', 'NOT SET')}")
sys.stdout.flush()
result = subprocess.run(
    ["python3", "-m", "ozon_pipeline.cli", "crawl-top-list-network",
     "--cdp-url", "http://127.0.0.1:9222",
     "--main-type", "hot",
     "--page-from", "1", "--page-to", "10",
     "--process-limit", "0", "--max-depth", "-1",
     "--retry-rejected-now"],
    cwd="/home/lx/ozon_selection_pipeline",
    env=os.environ.copy(),
)

print(f"Collection done, exit={result.returncode}")
b.close()
p.stop()
