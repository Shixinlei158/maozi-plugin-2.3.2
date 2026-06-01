"""连接浏览器，点框选主体按钮，截图看编辑模式 DOM 变化"""

import json
from playwright.sync_api import sync_playwright


def main():
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9224")

    context = browser.contexts[0]
    page = context.pages[0]
    print(f"页面: {page.title()} | URL: {page.url}")

    # 获取 1688 iframe
    import urllib.request
    resp = urllib.request.urlopen("http://127.0.0.1:9224/json")
    targets = json.loads(resp.read().decode())
    iframe_url = None
    for t in targets:
        url = t.get('url', '')
        if 'image-search' in url and '1688' in url:
            iframe_url = url
            break
    if not iframe_url:
        for t in targets:
            url = t.get('url', '')
            if 'aibuy.1688.com' in url:
                iframe_url = url
                break

    if not iframe_url:
        print("未找到 1688 iframe URL!")
        browser.close()
        pw.stop()
        return

    # 在新标签页打开 iframe URL
    new_page = context.new_page()
    new_page.goto(iframe_url, wait_until="domcontentloaded", timeout=60000)
    new_page.wait_for_timeout(8000)

    print(f"\n页面: {new_page.title()} | URL: {new_page.url[:100]}")

    # 1. 截图：点击前的状态
    new_page.screenshot(path="step0_before_click.png", full_page=False)
    print("截图: step0_before_click.png")

    # 2. 获取点击前的 cropper 元素
    before = new_page.evaluate("""() => {
        const els = document.querySelectorAll('[class*="cropper"], [class*="crop-"], [class*="selection"]');
        const result = [];
        for (const el of els) {
            const rect = el.getBoundingClientRect();
            if (rect && rect.width > 0 && rect.height > 0) {
                result.push({
                    tag: el.tagName,
                    class: (el.className || '').toString().substring(0, 120),
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                    style: el.getAttribute('style')?.substring(0, 200) || '',
                    cursor: window.getComputedStyle(el).cursor,
                    childCount: el.children.length,
                    text: (el.textContent || '').trim().substring(0, 40)
                });
            }
        }
        return result;
    }""")
    print(f"\n=== 点击前 cropper 元素 ({len(before)}) ===")
    print(json.dumps(before, ensure_ascii=False, indent=2))

    # 3. 点击框选主体按钮
    print("\n=== 点击框选主体按钮 ===")
    try:
        cut_btn = new_page.locator('[class*="cropper-cut-btn"]').first
        cut_btn.click()
        print("已点击框选主体按钮")
        new_page.wait_for_timeout(3000)
    except Exception as e:
        print(f"点击失败: {e}")

    # 4. 截图：点击后的状态
    new_page.screenshot(path="step1_after_click.png", full_page=False)
    print("截图: step1_after_click.png")

    # 5. 获取点击后的 DOM 变化
    after = new_page.evaluate("""() => {
        const result = {};

        // 所有 cropper 相关元素（变化对比）
        const els = document.querySelectorAll('[class*="cropper"], [class*="crop-"], [class*="selection"], [class*="modal"], [class*="dialog"], [class*="popup"], [class*="overlay"], [class*="mask"], [class*="handle"]');
        const cropperEls = [];
        for (const el of els) {
            const rect = el.getBoundingClientRect();
            if (rect && rect.width > 0 && rect.height > 0) {
                const style = window.getComputedStyle(el);
                cropperEls.push({
                    tag: el.tagName,
                    class: (el.className || '').toString().substring(0, 150),
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                    style: el.getAttribute('style')?.substring(0, 300) || '',
                    cursor: style.cursor,
                    visible: style.display !== 'none' && style.visibility !== 'hidden',
                    childCount: el.children.length,
                    text: (el.textContent || '').trim().substring(0, 40),
                    bg: style.backgroundColor?.substring(0, 50) || '',
                    zIndex: style.zIndex,
                    position: style.position
                });
            }
        }
        result.cropperElements = cropperEls;

        // 专门检查图片预览区和大图
        const imgs = document.querySelectorAll('img');
        const imgInfo = [];
        for (const img of imgs) {
            if (img.width > 50) {
                const rect = img.getBoundingClientRect();
                imgInfo.push({
                    src: img.src.substring(0, 80),
                    class: (img.className || '').toString().substring(0, 80),
                    w: img.width, h: img.height,
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                    style: img.getAttribute('style')?.substring(0, 200) || ''
                });
            }
        }
        result.largeImages = imgInfo;

        // 检查 canvas
        result.canvases = [];
        for (const c of document.querySelectorAll('canvas')) {
            const rect = c.getBoundingClientRect();
            result.canvases.push({
                w: c.width, h: c.height,
                rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                class: (c.className || '').toString().substring(0, 80),
                cursor: window.getComputedStyle(c).cursor
            });
        }

        // 特别检查：是否有新的模态/弹出
        const modals = [];
        for (const el of document.querySelectorAll('[class*="modal"], [class*="dialog"], [class*="popup"], [role="dialog"]')) {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (rect && rect.width > 100 && rect.height > 100 && style.display !== 'none') {
                modals.push({
                    tag: el.tagName,
                    class: (el.className || '').toString().substring(0, 120),
                    rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
                    text: (el.textContent || '').trim().substring(0, 100)
                });
            }
        }
        result.modals = modals;

        // 可拖拽的选框手柄（常见类名: n,e,s,w,ne,nw,se,sw resize handle）
        const handles = [];
        for (const el of document.querySelectorAll('[class*="handle"], [class*="n-resize"], [class*="s-resize"], [class*="e-resize"], [class*="w-resize"], [class*="ne-resize"], [class*="nw-resize"], [class*="se-resize"], [class*="sw-resize"]')) {
            const rect = el.getBoundingClientRect();
            handles.push({
                tag: el.tagName,
                class: (el.className || '').toString().substring(0, 100),
                rect: rect ? { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) } : null,
                cursor: window.getComputedStyle(el).cursor
            });
        }
        result.handles = handles;

        return result;
    }""")
    print(f"\n=== 点击后 DOM 状态 ===")
    print(json.dumps(after, ensure_ascii=False, indent=2)[:8000])

    # 6. 尝试用鼠标在图片上进行拖拽操作
    print("\n=== 尝试鼠标操作 ===")
    try:
        # 找到当前选区框的右下角手柄或边框
        drag_result = new_page.evaluate("""() => {
            // 找到所有 resizable handle
            const handles = document.querySelectorAll('[class*="handle"], [class*="resize"], [class*="n-"], [class*="s-"], [class*="e-"], [class*="w-"], [class*="ne-"], [class*="nw-"], [class*="se-"], [class*="sw-"]');
            const handleInfo = [];
            for (const h of handles) {
                const rect = h.getBoundingClientRect();
                handleInfo.push({
                    tag: h.tagName,
                    class: (h.className || '').toString(),
                    rect: rect ? { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) } : null,
                    cursor: window.getComputedStyle(h).cursor
                });
            }
            return handleInfo;
        }""")
        print(f"Resizer handles: {json.dumps(drag_result, ensure_ascii=False, indent=2)}")

        # 尝试找到大图并从中拖拽
        if after.get('largeImages'):
            for img in after['largeImages']:
                if 'cropper' in img.get('class', '').lower() or img.get('w', 0) > 100:
                    print(f"\n大图: {img.get('class')} | size={img.get('w')}x{img.get('h')} | rect={img.get('rect')}")

    except Exception as e:
        print(f"鼠标操作检测失败: {e}")

    # 7. 关键：检查 URL 中 imageRegion 参数的变化
    current_url = new_page.url
    print(f"\n当前 URL: {current_url}")
    if 'imageRegion' in current_url:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(current_url)
        params = parse_qs(parsed.query)
        print(f"imageRegion 参数: {params.get('imageRegion', 'N/A')}")

    new_page.close()
    browser.close()
    pw.stop()


if __name__ == "__main__":
    main()