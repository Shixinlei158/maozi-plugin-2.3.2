import requests, subprocess, sys

print("=== IP ===")
try:
    r = requests.get("http://httpbin.org/ip", timeout=10)
    print(f"Public IP: {r.json()['origin']}")
except Exception as e:
    print(f"IP check error: {e}")

print("\n=== Maozi API (direct) ===")
maozi_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiIsImp0aSI6IjA3M2MxOGUyZDgwZWQ3MmE4NjBlOTJlZWEyMGE1NDg2In0.eyJpc3MiOiJ6cXMiLCJhdWQiOiJBcHAgdXNlciIsImp0aSI6IjA3M2MxOGUyZDgwZWQ3MmE4NjBlOTJlZWEyMGE1NDg2IiwiaWF0IjoxNzc3Nzk2MTAwLCJuYmYiOjE3Nzc3OTYxMDAsImV4cCI6MTc4Mjk4MDEwMCwidWlkIjoxNjc4NTB9.jPABqvOlpiOv2QEpBT2pCaNdYZa6HqBhqPJN7FU7zdA"
try:
    r = requests.post(
        "https://api.maozierp.com/api.chrome/sku3",
        params={"sku": "1608725864"},
        json={"sku": "1608725864"},
        headers={"Authorization": f"Bearer {maozi_token}", "Content-Type": "application/json", "Client": "plugin", "Plugin-Version": "2.3.2"},
        timeout=15
    )
    print(f"Status: {r.status_code}, Size: {len(r.text)}")
    if r.status_code == 200:
        data = r.json()
        inner = data.get("data", {}).get("data", {})
        print(f"brand: {inner.get('brand')}, soldCount: {inner.get('soldCount')}")
    elif r.status_code == 403:
        print("403 Forbidden - MAOZI_TOKEN likely expired or IP blocked")
except Exception as e:
    print(f"Error: {e}")

print("\n=== Ozon (direct) ===")
try:
    r = requests.get("https://www.ozon.ru/", timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    print(f"Status: {r.status_code}, Size: {len(r.text)}")
    if "antibot" in r.text.lower() or "challenge" in r.text.lower():
        print("Anti-bot detected!")
except Exception as e:
    print(f"Error: {e}")

print("\n=== MySQL ===")
try:
    import pymysql
    from pymysql.cursors import DictCursor
    conn = pymysql.connect(host="100.97.110.39", port=3306, user="root", password="root", database="ozon_selection", cursorclass=DictCursor)
    cur = conn.cursor()
    cur.execute("SELECT MAX(last_seen_at) as t FROM sku_universe")
    row = cur.fetchone()
    print(f"Latest sku_universe: {row['t']}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
