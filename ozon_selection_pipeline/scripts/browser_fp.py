from playwright.sync_api import sync_playwright

p = sync_playwright().start()
browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
ctx = browser.contexts[0]
page = ctx.new_page()

# Check browser fingerprint details
print("=== Browser Fingerprint ===")
page.goto("about:blank")
info = page.evaluate("""() => {
    return {
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        hardwareConcurrency: navigator.hardwareConcurrency,
        deviceMemory: navigator.deviceMemory,
        maxTouchPoints: navigator.maxTouchPoints,
        webdriver: navigator.webdriver,
        languages: navigator.languages,
        plugins: navigator.plugins.length,
        webgl: (() => {
            try {
                const canvas = document.createElement('canvas');
                const gl = canvas.getContext('webgl');
                if (!gl) return 'NOT AVAILABLE';
                return gl.getParameter(gl.RENDERER);
            } catch(e) { return 'ERROR: ' + e.message; }
        })(),
        screen: `${screen.width}x${screen.height}`,
        colorDepth: screen.colorDepth,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    }
}""")
for k, v in info.items():
    print(f"  {k}: {v}")

page.close()
browser.close()
p.stop()
