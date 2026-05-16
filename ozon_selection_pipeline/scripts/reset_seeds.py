import pymysql
from pymysql.cursors import DictCursor
conn = pymysql.connect(host='localhost', user='root', password='root', database='ozon_selection', cursorclass=DictCursor)
cur = conn.cursor()
cur.execute("UPDATE seed_pool_skus SET last_processed_at=NULL, last_process_status=NULL, last_processed_snapshot_hash=NULL WHERE last_process_status='deferred' LIMIT 3")
conn.commit()
cur.execute("SELECT COUNT(*) as cnt FROM seed_pool_skus WHERE last_processed_at IS NULL")
row = cur.fetchone()
print(f"reset 3 rows, now {row['cnt']} due")
conn.close()
