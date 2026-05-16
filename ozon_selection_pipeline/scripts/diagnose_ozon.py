import subprocess, requests, time

# Test 1: OpenSSL direct (already works)
print("=== Test 1: OpenSSL (Python requests) 直连 ===")
try:
    r = requests.get("https://www.ozon.ru/", timeout=15, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"})
    print(f"  Status: {r.status_code}, Size: {len(r.text)}")
    if "antibot" in r.text.lower() or "challenge" in r.text.lower():
        print("  -> Anti-bot page")
    else:
        print(f"  -> Title: {r.text[:200]}")
except Exception as e:
    print(f"  Error: {type(e).__name__}: {e}")

# Test 2: wget direct
print("\n=== Test 2: wget 直连 ===")
result = subprocess.run(["wget", "-qO-", "--timeout=10", "https://www.ozon.ru/"], capture_output=True, text=True, timeout=15)
print(f"  stdout size: {len(result.stdout)}, stderr: {result.stderr[:100]}")

# Test 3: Check if it's a TCP RST issue
print("\n=== Test 3: TCP连接测试 ===")
import socket, ssl
ctx = ssl.create_default_context()
try:
    sock = socket.create_connection(("www.ozon.ru", 443), timeout=10)
    ssock = ctx.wrap_socket(sock, server_hostname="www.ozon.ru")
    print(f"  TLS version: {ssock.version()}")
    print(f"  Cipher: {ssock.cipher()}")
    # Send HTTP request
    ssock.sendall(b"GET / HTTP/1.1\r\nHost: www.ozon.ru\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\nAccept: text/html\r\nConnection: close\r\n\r\n")
    resp = b""
    while True:
        data = ssock.recv(4096)
        if not data:
            break
        resp += data
        if len(resp) > 10000:
            break
    print(f"  Response size: {len(resp)}")
    first_line = resp.split(b"\r\n")[0].decode()
    print(f"  Status line: {first_line}")
    ssock.close()
except Exception as e:
    print(f"  Error: {type(e).__name__}: {e}")

# Test 4: Check Chrome without proxy
print("\n=== Test 4: Chrome无代理测试 ===")
try:
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = browser.contexts[0]
    page = ctx.new_page()
    try:
        page.goto("https://httpbin.org/ip", wait_until="domcontentloaded", timeout=15000)
        print(f"  httpbin response: {page.evaluate('() => document.body.innerText')}")
    except Exception as e:
        print(f"  httpbin error: {e}")
    page.close()
    browser.close()
    p.stop()
except Exception as e:
    print(f"  CDP error: {type(e).__name__}: {e}")
