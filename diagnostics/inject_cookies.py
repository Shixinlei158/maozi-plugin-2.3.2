import requests
from playwright.sync_api import sync_playwright

r = requests.get("https://www.ozon.ru/", timeout=15, headers={"User-Agent": "Mozilla/5.0"})
cookies = r.cookies
print(f"Got {len(cookies)} cookies from requests session")

cookies_to_set = []
for cookie in cookies:
    cookies_to_set.append({
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain or ".ozon.ru",
        "path": cookie.path or "/",
        "httpOnly": cookie.has_nonstandard_attr("HttpOnly") if hasattr(cookie, 'has_nonstandard_attr') else False,
        "secure": cookie.secure or True,
        "sameSite": "None",
    })

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]
    
    for c in cookies_to_set:
        print(f"Setting: {c['name']}")
        context.add_cookies([c])
    
    page = context.new_page()
    print("Navigating with injected cookies...")
    try:
        resp = page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=30000)
        print(f"Status: {resp.status}")
        print(f"Title: {page.title()[:100]}")
        if resp.status == 200:
            print("SUCCESS! Ozon page loaded normally.")
        elif resp.status == 403:
            body = page.evaluate("() => document.body ? document.body.innerText.substring(0,200) : 'no body'")
            print(f"Body: {body}")
    except Exception as e:
        print(f"Error: {e}")
    
    page.close()
    browser.close()
