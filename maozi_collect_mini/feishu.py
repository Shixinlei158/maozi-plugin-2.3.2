"""飞书机器人告警通知。

功能：
- 发送文本/错误告警到飞书群
- 支持 Markdown 格式
- Token 通过环境变量 FEISHU_WEBHOOK_URL 配置
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import requests

from .config import settings


def _post_webhook(payload: dict[str, Any]) -> bool:
    """发送飞书 Webhook 消息"""
    url = settings.feishu_webhook_url
    if not url:
        return False
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def send_text(text: str) -> bool:
    """发送纯文本消息"""
    return _post_webhook({
        "msg_type": "text",
        "content": {"text": text},
    })


def send_notification(subject: str, level: str = "INFO") -> bool:
    """发送格式化告警通知

    Args:
        subject: 告警主题
        level: 告警级别 INFO/WARN/ERROR
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    emoji = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "🚨"}.get(level, "📢")

    content = f"{emoji} **毛子采集 Mini 告警**\n"
    content += f"时间: {now}\n"
    content += f"级别: {level}\n"
    content += f"详情: {subject}\n"

    return send_text(content)


def send_error_notification(subject: str) -> bool:
    """发送错误告警（快捷方法）"""
    return send_notification(subject, level="ERROR")
