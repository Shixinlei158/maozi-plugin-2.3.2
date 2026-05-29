import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ozon_pipeline.browser_ozon import BrowserOzonClient
from ozon_pipeline.repository import upsert_seller_home_sku

client = BrowserOzonClient()
print("Checking upsert_seller_home_sku output for a0403...")
url = "https://www.ozon.ru/seller/a0403/"

try:
    res = client.seller_home_products(url, max_scrolls=2)
    items = res.get("items", [])
    print(f"Items found: {len(items)}")
    
    selected_items = items[:0 or None]
    print(f"Selected items: {len(selected_items)}")
    
    prepared_items = []
    for item in selected_items[:5]:
        sku = upsert_seller_home_sku(url, item)
        print(f"Item: {item.get('href')} -> sku: {sku}")
        if sku:
            prepared_items.append((sku, item))
            
    print(f"Prepared items: {len(prepared_items)}")
except Exception as e:
    print(f"Error: {e}")