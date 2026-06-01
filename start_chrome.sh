#!/bin/bash
# 启动Chrome on lx
pkill -f chrome 2>/dev/null
sleep 2
DISPLAY=:99 ~/chrome/opt/google/chrome/chrome \
    --remote-debugging-port=9222 \
    --user-data-dir=/tmp/chrome-test2 \
    --no-first-run --no-sandbox --disable-dev-shm-usage \
    --disable-gpu --disable-software-rasterizer \
    --disable-extensions --disable-blink-features=AutomationControlled \
    --window-size=1920,1080 about:blank > /tmp/chrome3.log 2>&1 &
sleep 8
curl -s --max-time 5 http://127.0.0.1:9222/json/version
ps -ef | grep chrome | grep -v grep | wc -l
