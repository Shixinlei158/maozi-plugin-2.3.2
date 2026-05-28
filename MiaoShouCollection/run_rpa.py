"""妙手采集 - 批量循环RPA脚本（页面复用版）

业务逻辑：
1. 从数据库批量获取待图搜的SKU
2. 连接浏览器，打开一个页面
3. 对每个SKU执行：图搜→筛选→保存→切换tab复位页面
"""

import json
import time
import pymysql
from playwright.sync_api import sync_playwright


CDP_URL = "http://127.0.0.1:9224"
TARGET_URL = "https://erp.91miaoshou.com/common_collect_box/index?fetchType=aliCrossSaleSports"

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "root",
    "database": "ozon_selection",
}

IMAGE_SEARCH_API = "mtop.com.alibaba.global.select.aibuy.image.search"
FILTERS = ["一件代发", "24H发货", "件重尺已校准"]


def get_pending_skus(limit: int):
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT sku, main_image_url FROM sku_products "
                "WHERE main_image_url IS NOT NULL "
                "AND alibaba_image_search_response IS NULL "
                "LIMIT %s", (limit,)
            )
            return cursor.fetchall()
    finally:
        conn.close()


def parse_response(response_data):
    image_links = []
    detail_urls = []
    try:
        data = response_data.get("data", {})
        items = data.get("data", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        for item in items:
            pic_url = item.get("imageUrl", "")
            detail_url = item.get("offerDetailUrl", "") or item.get("link", "")
            item_id = item.get("itemId", "")
            if pic_url and detail_url:
                image_links.append({"picurl": pic_url, "1688url": detail_url, "itemId": item_id})
                detail_urls.append(detail_url)
    except Exception as e:
        print(f"[解析] 失败: {e}")
    return image_links, detail_urls


def save_to_db(sku, image_links, detail_urls, responses):
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE sku_products SET "
                "alibaba_image_links = %s, "
                "alibaba_detail_urls = %s, "
                "alibaba_image_search_response = %s "
                "WHERE sku = %s",
                (
                    json.dumps(image_links, ensure_ascii=False),
                    json.dumps(detail_urls, ensure_ascii=False),
                    json.dumps(responses, ensure_ascii=False),
                    sku,
                ),
            )
        conn.commit()
        print(f"[DB] 已写入: sku={sku}, {len(image_links)}张图")
    except Exception as e:
        print(f"[DB] 写入失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def reset_page(iframe, page):
    """切换tab复位页面，回到可点击图片链接搜索的状态"""
    try:
        # 1. 点击 "妙手-1688精翻货盘"
        page.locator("text=妙手-1688精翻货盘").first.click()
        page.wait_for_timeout(1500)
        print("[复位] 已点击妙手-1688精翻货盘")
    except Exception as e:
        print(f"[复位] 点击精翻货盘失败: {e}")

    try:
        # 2. 点击 "1688跨境热卖现货"
        page.locator("text=1688跨境热卖现货").first.click()
        page.wait_for_timeout(3000)
        print("[复位] 已点击1688跨境热卖现货")
    except Exception as e:
        print(f"[复位] 点击跨境热卖失败: {e}")

    # 重新获取iframe
    for attempt in range(10):
        for f in page.frames:
            if "aibuy.1688.com" in f.url:
                print("[复位] iframe已恢复")
                return f
        page.wait_for_timeout(1000)
    return None


def do_image_search(iframe, page, image_url, captured):
    """执行一次图搜完整流程，捕获的响应追加到captured列表"""
    start_count = len(captured)

    # 点击图片链接搜索
    try:
        iframe.locator("text=图片链接搜索").first.click()
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"[错误] 点击图片链接搜索失败: {e}")
        return captured

    # 填入URL
    try:
        textarea = iframe.locator("textarea").first
        textarea.wait_for(state="visible", timeout=15000)
        textarea.fill(image_url)
        page.wait_for_timeout(500)
    except Exception as e:
        print(f"[错误] 填入失败: {e}")
        return captured

    # 点确定
    try:
        iframe.locator('span:has-text("确定")').first.click()
        page.wait_for_timeout(8000)
    except Exception as e:
        print(f"[错误] 点击确定失败: {e}")
        return captured

    # 设置筛选条件
    try:
        dropdowns = iframe.locator('[class*="select"]').all()
        for dropdown in dropdowns:
            text = dropdown.text_content() or ""
            if "商品信息" in text or "请选择" in text:
                dropdown.click()
                page.wait_for_timeout(500)
                break

        for f in FILTERS:
            try:
                iframe.locator(f"text={f}").first.click()
                page.wait_for_timeout(300)
            except Exception:
                pass

        try:
            iframe.locator("text=图搜结果").first.click(timeout=2000)
        except Exception:
            pass
    except Exception as e:
        print(f"[筛选] 失败: {e}")

    # 等待响应
    page.wait_for_timeout(5000)
    return captured[start_count:]  # 只返回新捕获的


def main():
    limit = 10
    rows = get_pending_skus(limit)
    if not rows:
        print("没有待图搜的SKU")
        return

    print(f"待处理: {len(rows)} 个SKU")

    with sync_playwright() as p:
        print("[连接] 连接浏览器...")
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        
        print("[导航] 打开采集页面...")
        page = context.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"[页面] {page.title()}")
        page.wait_for_timeout(3000)

        # 全局响应监听（整个循环复用）
        captured_responses = []

        def on_response(response):
            if IMAGE_SEARCH_API in response.url:
                try:
                    body = response.json()
                    captured_responses.append(body)
                except Exception:
                    pass

        context.on("response", on_response)

        # 等待iframe加载
        iframe = None
        for attempt in range(10):
            page.wait_for_timeout(2000)
            for f in page.frames:
                if "aibuy.1688.com" in f.url:
                    iframe = f
                    break
            if iframe:
                print("[iframe] 已找到")
                break
            print(f"[iframe] 等待中 ({attempt+1}/10)...")

        if not iframe:
            print("未找到1688 iframe")
            browser.close()
            return

        success = 0
        fail = 0

        for i, (sku, image_url) in enumerate(rows):
            if i > 0:
                iframe = reset_page(iframe, page)
                if not iframe:
                    fail += 1
                    continue

            print(f"\n[{i+1}/{len(rows)}] SKU: {sku}")

            responses = do_image_search(iframe, page, image_url, captured_responses)

            if responses:
                search_resp = responses[0]
                image_links, detail_urls = parse_response(search_resp)
                save_to_db(sku, image_links, detail_urls, responses)
                print(f"  结果: {len(image_links)}张图, {len(detail_urls)}个链接")
                success += 1
            else:
                print(f"  失败: 未捕获到响应")
                fail += 1

        print(f"\n===== 完成: 成功={success}, 失败={fail} =====")
        browser.close()


if __name__ == "__main__":
    main()
