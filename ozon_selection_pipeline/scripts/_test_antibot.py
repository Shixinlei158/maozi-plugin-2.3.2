from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    page = browser.contexts[0].pages[0]
    page.goto("https://www.ozon.ru", timeout=15000)
    page.wait_for_timeout(3000)
    html = page.content()
    for m in ["antibot", "challenge", "captcha", "cloudflare", "cf-", "checking", "security"]:
        if m.lower() in html.lower():
            idx = html.lower().find(m.lower())
            print(f"Found '{m}' at pos {idx}: ...{html[max(0,idx-20):idx+60]}...")
    print("Page URL:", page.url)
    print("Title:", page.title())
    browser.close()
