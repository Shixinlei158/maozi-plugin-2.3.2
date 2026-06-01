#!/bin/bash
# lx 服务器部署脚本（从本机执行）
# 用法: bash deploy_lx.sh
set -e

LX_HOST="100.97.110.39"
LX_USER="ubuntu"
LX_HOME="/home/ubuntu"
PROJECT="ozon_selection_pipeline"

echo "=== 部署 $PROJECT 到 lx ==="

# 1. 通过 git 同步代码（lx 上先 clone 过则 pull，否则 clone）
echo "[1/4] 同步代码..."
ssh "$LX_USER@$LX_HOST" "
  if [ -d $LX_HOME/$PROJECT/.git ]; then
    cd $LX_HOME/$PROJECT && git pull
  else
    echo '请先在 lx 上 git clone 项目'
    exit 1
  fi
"

# 2. 应用 lx 专用 .env
echo "[2/4] 应用 lx 配置..."
scp ozon_selection_pipeline/.env.lx "$LX_USER@$LX_HOST:$LX_HOME/$PROJECT/.env"

# 3. 同步数据库表结构
echo "[3/4] 同步数据库表结构..."
ssh "$LX_USER@$LX_HOST" "cd $LX_HOME/$PROJECT && python3.9 -c '
from ozon_pipeline.db_sync import sync_table_structure
from ozon_pipeline.config import DB_PROFILES
result = sync_table_structure(DB_PROFILES[\"local\"])
print(f\"表结构同步: {result[\"files\"]} 文件, {result[\"statements\"]} 条SQL, 错误={len(result[\"errors\"])}\")
for e in result[\"errors\"][:5]:
    print(f\"  错误: {e}\")
'"

# 4. 验证
echo "[4/4] 验证..."
ssh "$LX_USER@$LX_HOST" "cd $LX_HOME/$PROJECT && python3.9 -c '
from ozon_pipeline import db
r = db.fetch_one(\"SELECT COUNT(*) AS cnt FROM seller_shops\")
print(f\"DB正常, seller_shops: {r[\"cnt\"]} 条\")
from ozon_pipeline.config import _detect_db_profile
print(f\"DB Profile: {_detect_db_profile()}\")
'"

echo ""
echo "=== 部署完成 ==="
echo "启动浏览器: bash ~/browser-fingerprint-spoofing/start_ozon_browser.sh"
echo "启动采集:  cd ~/ozon_selection_pipeline && python3.9 -m ozon_pipeline.cli crawl-top-list-network --cdp-url http://127.0.0.1:9222 --main-type hot --page-from 1 --page-to 20"
