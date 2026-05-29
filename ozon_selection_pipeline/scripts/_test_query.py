import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ozon_pipeline.browser_ozon import BrowserOzonClient

client = BrowserOzonClient()
print("Starting visual feedback test for angelcity-1445659...")
url = "https://www.ozon.ru/seller/angelcity-1445659/"

try:
    print(f"Fetching products for: {url}")
    res = client.seller_home_products(url, max_scrolls=2)
    items = res.get("items", [])
    print(f"Success! Source: {res.get('source')}, Items found: {len(items)}")
    
    if items:
        test_item = items[0]
        print(f"First item data: {test_item}")

except Exception as e:
    print(f"Test failed with error: {e}")
    import traceback
    traceback.print_exc()
