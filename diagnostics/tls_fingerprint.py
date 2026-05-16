"""Compare TLS fingerprint between this machine and what's expected"""
from playwright.sync_api import sync_playwright
import json

p = sync_playwright().start()
browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
ctx = browser.contexts[0]
page = ctx.new_page()

# Test 1: Check TLS fingerprint via tls.peet.ws
print("=== TLS Fingerprint Test ===")
try:
    page.goto("https://tls.peet.ws/api/all", wait_until="domcontentloaded", timeout=15000)
    body = page.evaluate("() => document.body.innerText")
    data = json.loads(body)
    print(f"  JA3: {data.get('tls', {}).get('ja3', 'N/A')[:80]}")
    print(f"  JA3 hash: {data.get('tls', {}).get('ja3_hash', 'N/A')}")
    print(f"  JA4: {data.get('tls', {}).get('ja4', 'N/A')}")
    print(f"  HTTP/2 akamai fp: {data.get('http2', {}).get('akamai_fingerprint', 'N/A')}")
    print(f"  User-Agent: {data.get('http_version', 'N/A')}")
    print(f"  IP: {data.get('ip', 'N/A')}")
except Exception as e:
    print(f"  Error: {type(e).__name__}: {e}")

# Test 2: Check what Ozon sees
print("\n=== Ozon Response Headers ===")
try:
    resp = page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=20000)
    print(f"  Status: {resp.status}")
    headers = resp.all_headers()
    for k, v in headers.items():
        if k.lower() in ("server", "cf-ray", "cf-cache-status", "x-o3-app-name", "set-cookie"):
            print(f"  {k}: {v[:80]}")
    print(f"  URL: {page.url}")
    print(f"  Title: {page.title()}")
except Exception as e:
    print(f"  Error: {type(e).__name__}: {e}")

page.close()
browser.close()
p.stop()
