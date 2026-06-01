"""飞书自定义机器人通知模块

通过 webhook 发送采集异常/停止通知到飞书群。
非阻塞发送：通知失败不影响主采集流程。
"""
from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime
from typing import Any

import requests

from .config import settings


_SEVERITY_STYLES = {
    "error": {
        "color": "red",
        "icon": "❌",
        "label": "严重",
    },
    "warning": {
        "color": "orange",
        "icon": "⚠",
        "label": "警告",
    },
    "info": {
        "color": "blue",
        "icon": "ℹ",
        "label": "通知",
    },
}


def _build_card(
    title: str,
    content_fields: list[dict[str, str]],
    severity: str = "error",
) -> dict:
    style = _SEVERITY_STYLES.get(severity, _SEVERITY_STYLES["error"])
    header_title = f"{style['icon']} {title}"

    lines: list[str] = []
    for field in content_fields:
        label = field.get("label", "")
        value = field.get("value", "")
        if label:
            lines.append(f"**{label}**：{value}")
        else:
            lines.append(value)
    body_md = "\n".join(lines)

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": style["color"],
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": body_md,
                    "text_align": "left",
                    "text_size": "normal_v2",
                }
            ],
        },
    }


def _send_webhook(webhook_url: str, payload: dict) -> bool:
    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        result = resp.json()
        if result.get("code") == 0:
            return True
        print(f"[feishu] webhook 返回错误: code={result.get('code')} msg={result.get('msg')}")
        return False
    except Exception:
        print(f"[feishu] webhook 请求异常: {traceback.format_exc(limit=2)}")
        return False


def send_notification(
    title: str,
    fields: list[dict[str, str]],
    severity: str = "error",
) -> None:
    """异步发送飞书通知（非阻塞）。

    Args:
        title: 通知标题
        fields: 内容字段列表，每项为 {"label": "字段名", "value": "字段值"}
        severity: 严重级别 ("error" | "warning" | "info")
    """
    webhook_url = settings.feishu_webhook_url
    if not webhook_url:
        return

    card = _build_card(title, fields, severity=severity)
    payload = {
        "msg_type": "interactive",
        "card": card,
    }

    t = threading.Thread(
        target=_send_webhook,
        args=(webhook_url, payload),
        daemon=True,
        name="feishu-notify",
    )
    t.start()


def _fmt_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def notify_collection_stopped(
    mode: str,
    reason: str,
    *,
    round_no: int = 0,
    detail: str = "",
    extra_fields: list[dict[str, str]] | None = None,
    exc: Exception | None = None,
    severity: str = "error",
) -> None:
    """发送采集停止通知（含完整上下文）。

    Args:
        mode: 采集模式名称
        reason: 停止原因简述
        round_no: 当前轮次号
        detail: 补充详情
        extra_fields: 额外统计字段
        exc: 相关异常对象
        severity: 严重级别
    """
    fields: list[dict[str, str]] = [
        {"label": "采集模式", "value": mode},
        {"label": "停止时间", "value": _fmt_time()},
        {"label": "停止原因", "value": reason},
    ]
    if round_no > 0:
        fields.append({"label": "当前轮次", "value": str(round_no)})
    if detail:
        fields.append({"label": "详细信息", "value": detail})
    if extra_fields:
        fields.extend(extra_fields)
    if exc is not None:
        exc_info = f"{type(exc).__name__}: {exc}"
        fields.append({"label": "异常信息", "value": exc_info})

    send_notification(f"采集停止 — {reason}", fields, severity=severity)


def notify_collection_failed(
    mode: str,
    *,
    round_no: int = 0,
    detail: str = "",
    extra_fields: list[dict[str, str]] | None = None,
    exc: Exception | None = None,
    stats: dict[str, int] | None = None,
) -> None:
    """发送采集异常通知。

    Args:
        mode: 采集模式名称
        round_no: 当前轮次
        detail: 补充详情
        extra_fields: 额外统计字段
        exc: 异常对象
        stats: 累计统计 {"total_sellers": 10, "total_skus": 100, ...}
    """
    fields: list[dict[str, str]] = [
        {"label": "采集模式", "value": mode},
        {"label": "异常时间", "value": _fmt_time()},
    ]
    if round_no > 0:
        fields.append({"label": "当前轮次", "value": str(round_no)})
    if detail:
        fields.append({"label": "详细信息", "value": detail})
    if exc is not None:
        exc_info = f"{type(exc).__name__}: {exc}"
        fields.append({"label": "异常类型", "value": exc_info})
        tb_str = "".join(traceback.format_tb(exc.__traceback__))
        fields.append({"label": "异常堆栈", "value": tb_str})
    if extra_fields:
        fields.extend(extra_fields)
    if stats:
        parts = []
        for k, v in stats.items():
            parts.append(f"{k}: {v}")
        fields.append({"label": "累计统计", "value": " / ".join(parts)})

    reason = f"采集异常{(' — ' + str(exc)) if exc else ''}"
    send_notification(reason, fields, severity="error")


def notify_collection_warning(
    mode: str,
    warning: str,
    *,
    round_no: int = 0,
    detail: str = "",
    extra_fields: list[dict[str, str]] | None = None,
) -> None:
    """发送采集警告通知。

    Args:
        mode: 采集模式名称
        warning: 警告内容
        round_no: 当前轮次
        detail: 补充详情
        extra_fields: 额外统计字段
    """
    fields: list[dict[str, str]] = [
        {"label": "采集模式", "value": mode},
        {"label": "警告时间", "value": _fmt_time()},
        {"label": "警告内容", "value": warning},
    ]
    if round_no > 0:
        fields.append({"label": "当前轮次", "value": str(round_no)})
    if detail:
        fields.append({"label": "详细信息", "value": detail})
    if extra_fields:
        fields.extend(extra_fields)

    send_notification(f"采集警告 — {warning}", fields, severity="warning")
