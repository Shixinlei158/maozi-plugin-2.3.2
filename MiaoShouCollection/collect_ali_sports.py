"""妙手采集 - 1688图搜采集脚本

业务逻辑：
1. 从数据库读取Ozon商品主图URL和SKU
2. 在妙手ERP的图搜页面，用该图片搜索1688同款
3. 在图搜结果中设置筛选条件
4. 监听1688图搜API响应
5. 解析响应，提取图片链接和1688详情链接
6. 将结果写回数据库sku_products表
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


def get_pending_sku():
    """从sku_products获取待图搜的SKU（有主图但未图搜）"""
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT sku, main_image_url FROM sku_products "
                "WHERE main_image_url IS NOT NULL "
                "AND alibaba_image_search_response IS NULL "
                "LIMIT 1"
            )
            return cursor.fetchone()
    finally:
        conn.close()


def parse_response(response_data):
    """解析图搜API响应，提取图片链接和详情链接"""
    image_links = []
    detail_urls = []

    try:
        items = response_data.get("data", {}).get("data", [])
        for item in items:
            pic_url = item.get("imageUrl", "")
            detail_url = item.get("offerDetailUrl", "") or item.get("link", "")
            item_id = item.get("itemId", "")

            if pic_url and detail_url:
                image_links.append({
                    "picurl": pic_url,
                    "1688url": detail_url,
                    "itemId": item_id,
                })
                detail_urls.append(detail_url)
    except Exception as e:
        print(f"解析响应失败: {e}")

    return image_links, detail_urls


def save_to_db(sku_id, image_links, detail_urls, response_data):
    """将图搜结果写入数据库"""
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
                    json.dumps(response_data, ensure_ascii=False),
                    sku_id,
                ),
            )
        conn.commit()
        print(f"已写入数据库: sku={sku_id}, {len(image_links)}张图, {len(detail_urls)}个链接")
    except Exception as e:
        print(f"写入数据库失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def connect_browser():
    """连接到9224端口的Chrome浏览器"""
    playwright = sync_playwright().start()
    browser = playwright.chromium.connect_over_cdp(CDP_URL)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return playwright, browser, context


def image_search(page, iframe, image_url):
    """在iframe弹窗中执行图片搜索"""
    try:
        iframe.locator("text=图片链接搜索").first.click()
        print("已点击图片链接搜索")
        page.wait_for_timeout(1000)
    except Exception as e:
        print(f"点击图片链接搜索失败: {e}")
        return False

    try:
        textarea = iframe.locator("textarea").first
        textarea.wait_for(state="visible", timeout=5000)
        textarea.fill(image_url)
        print(f"已填入图片链接: {image_url}")
        page.wait_for_timeout(500)
    except Exception as e:
        print(f"填入图片链接失败: {e}")
        return False

    try:
        iframe.locator('span:has-text("确定")').first.click()
        print("已点击确定")
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"点击确定失败: {e}")
        return False

    return True


def select_image_search_filters(iframe, page):
    """在图搜结果中多选筛选条件"""
    filters = ["一件代发", "24H发货", "件重尺已校准"]

    try:
        dropdowns = iframe.locator('[class*="select"]').all()
        for dropdown in dropdowns:
            text = dropdown.text_content() or ""
            if "商品信息" in text or "请选择" in text:
                dropdown.click()
                page.wait_for_timeout(500)
                print("已打开商品信息下拉框")
                break

        for text in filters:
            try:
                iframe.locator(f"text={text}").first.click()
                print(f"已勾选: {text}")
                page.wait_for_timeout(300)
            except Exception as e:
                print(f"勾选 {text} 失败: {e}")

        try:
            iframe.locator("text=图搜结果").first.click()
            page.wait_for_timeout(500)
        except Exception:
            pass
        print("筛选条件已设置")
    except Exception as e:
        print(f"设置筛选条件失败: {e}")


def main():
    # 1. 从数据库获取待图搜的SKU
    row = get_pending_sku()
    if not row:
        print("没有待图搜的SKU")
        return

    sku_id, image_url = row
    print(f"SKU ID: {sku_id}, 图片URL: {image_url}")

    # 2. 连接浏览器
    playwright, browser, context = connect_browser()
    captured = []

    try:
        # 3. 设置响应监听
        def on_response(response):
            if IMAGE_SEARCH_API in response.url:
                try:
                    body = response.json()
                    captured.append(body)
                    print("捕获到图搜响应")
                except Exception as e:
                    print(f"解析响应失败: {e}")

        context.on("response", on_response)

        # 4. 导航到采集页面
        page = context.new_page()
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        print(f"页面已加载: {page.title()}")

        # 等待 iframe 加载
        print("等待 iframe 加载...")
        iframe = None
        for attempt in range(10):
            page.wait_for_timeout(2000)
            print(f"检测 frames ({attempt+1}/10)...")
            for f in page.frames:
                print(f"  frame: {f.url[:80]}")
                if "aibuy.1688.com" in f.url or "1688" in f.url:
                    iframe = f
                    print(f"找到 1688 iframe: {f.url[:80]}")
                    break
            if iframe:
                break

        if not iframe:
            print("未找到1688 iframe")
            return

        # 5. 执行图片搜索
        if not image_search(page, iframe, image_url):
            return
        page.wait_for_timeout(5000)

        # 6. 设置图搜结果筛选条件
        select_image_search_filters(iframe, page)
        print("图搜筛选条件已设置")

        # 7. 等待响应并入库
        page.wait_for_timeout(5000)
        if captured:
            resp = captured[-1]  # 取最新的响应
            image_links, detail_urls = parse_response(resp)
            save_to_db(sku_id, image_links, detail_urls, resp)
            print(f"完成: {len(image_links)}张图, {len(detail_urls)}个链接")
        else:
            print("未捕获到响应")

    finally:
        browser.close()
        playwright.stop()


if __name__ == "__main__":
    main()
