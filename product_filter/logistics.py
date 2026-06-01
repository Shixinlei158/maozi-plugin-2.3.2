"""
物流费率解析：从 GUOO realFBS 资费试算表中提取渠道费率，并根据产品属性匹配适用渠道
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
import re

import openpyxl

from .config import settings


@dataclass
class LogisticsChannel:
    """单个物流渠道的费率信息"""
    row: int  # Excel行号，方便回溯
    product_type: str  # 产品类型（Extra Small / Budget / Small / Big / Premium Small / ...）
    delivery_name: str  # 配送方式名称
    transport_mode: str  # 运输方式（空运/陆运/陆空联运）
    per_kg_rate: Decimal  # 每千克运费(元)
    per_ticket_rate: Decimal  # 每票运费(元)
    weight_min_kg: Optional[Decimal] = None  # 重量下限(kg)
    weight_max_kg: Optional[Decimal] = None  # 重量上限(kg)
    value_min_rub: Optional[Decimal] = None  # 货值下限(卢布)
    value_max_rub: Optional[Decimal] = None  # 货值上限(卢布)


def _parse_weight_range(text: Optional[str]) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """解析重量区间，如 '0.001-0.5KG' → (0.001, 0.5)"""
    if not text:
        return None, None
    text = str(text).strip()
    m = re.search(r"([\d.]+)\s*-\s*([\d.]+)\s*(?:KG|kg)", text)
    if m:
        return Decimal(m.group(1)), Decimal(m.group(2))
    # 单值如 "收抛" 表示重量抛货，返回 None
    return None, None


def _parse_value_range(text: Optional[str]) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """解析货值区间，如 '1-1500₽' → (1, 1500)"""
    if not text:
        return None, None
    text = str(text).strip()
    # 匹配 "1-1500₽" 或 "7001-250000₽"
    m = re.search(r"([\d,.]+)\s*-\s*([\d,.]+)\s*[₽$]?", text)
    if m:
        return Decimal(m.group(1).replace(",", "")), Decimal(m.group(2).replace(",", ""))
    # 匹配 "不超120美金" 这类
    m = re.search(r"不超\s*([\d,.]+)", text)
    if m:
        return Decimal("0"), Decimal(m.group(1).replace(",", ""))
    return None, None


def load_logistics_table(
    excel_path: Optional[str] = None,
    sheet_name: Optional[str] = None,
) -> list[LogisticsChannel]:
    """
    从 Excel 物流资费表中提取所有 realFBS 渠道。
    子渠道（同产品类型下无自己的限制字段的行）会继承父行的重量/货值/尺寸限制。
    返回有序列表(按Excel行顺序)。
    """
    excel_path = excel_path or settings.logistics_excel_path
    sheet_name = sheet_name or settings.logistics_sheet_name

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb[sheet_name]

    channels: list[LogisticsChannel] = []

    # 继承状态：每个产品类型组的第一行会设定限制，子行继承
    inherited_weight_min: Optional[Decimal] = None
    inherited_weight_max: Optional[Decimal] = None
    inherited_value_min: Optional[Decimal] = None
    inherited_value_max: Optional[Decimal] = None

    for r in range(10, 35):  # 渠道数据在10-34行
        per_kg = ws.cell(r, 11).value  # K列
        per_ticket = ws.cell(r, 12).value  # L列

        if per_kg is None and per_ticket is None:
            continue

        product_type = str(ws.cell(r, 2).value or "").replace("\n", " ").strip()
        delivery = str(ws.cell(r, 3).value or "").split("\n")[0].strip()
        transport = str(ws.cell(r, 4).value or "").strip()

        weight_range_text = str(ws.cell(r, 7).value or "")
        value_range_text = str(ws.cell(r, 8).value or "")

        w_min, w_max = _parse_weight_range(weight_range_text)
        v_min, v_max = _parse_value_range(value_range_text)

        # 如果当前行有产品类型名称（非空），说明是新一组开始，更新继承状态
        if product_type:
            inherited_weight_min = w_min
            inherited_weight_max = w_max
            inherited_value_min = v_min
            inherited_value_max = v_max
        else:
            # 子行：自己有限制就用自己的，否则继承父行
            if w_min is None:
                w_min = inherited_weight_min
            if w_max is None:
                w_max = inherited_weight_max
            if v_min is None:
                v_min = inherited_value_min
            if v_max is None:
                v_max = inherited_value_max

        channels.append(LogisticsChannel(
            row=r,
            product_type=product_type,
            delivery_name=delivery,
            transport_mode=transport,
            per_kg_rate=Decimal(str(per_kg or 0)),
            per_ticket_rate=Decimal(str(per_ticket or 0)),
            weight_min_kg=w_min,
            weight_max_kg=w_max,
            value_min_rub=v_min,
            value_max_rub=v_max,
        ))

    wb.close()
    return channels


def _channel_match_score(
    channel: LogisticsChannel,
    weight_kg: Decimal,
    price_rub: Optional[Decimal],
) -> tuple[bool, int]:
    """
    返回 (是否匹配, 匹配精度分数)。
    分数越高 = 约束越精确匹配（同时满足重量和货值 > 仅满足一项）
    """
    score = 0
    # 重量检查
    if channel.weight_min_kg is not None and channel.weight_max_kg is not None:
        if weight_kg < channel.weight_min_kg or weight_kg > channel.weight_max_kg:
            return False, 0
        score += 1  # 有明确重量区间且满足
    elif channel.weight_min_kg is not None and weight_kg < channel.weight_min_kg:
        return False, 0
    elif channel.weight_max_kg is not None and weight_kg > channel.weight_max_kg:
        return False, 0
    # 重量无约束：score不加分

    # 货值检查
    if channel.value_min_rub is not None and channel.value_max_rub is not None:
        if price_rub is None:
            return False, 0  # 渠道有货值要求但无法判断 → 不匹配
        if price_rub < channel.value_min_rub or price_rub > channel.value_max_rub:
            return False, 0
        score += 1  # 有明确货值区间且满足
    elif channel.value_min_rub is not None:
        if price_rub is not None and price_rub < channel.value_min_rub:
            return False, 0
    elif channel.value_max_rub is not None:
        if price_rub is not None and price_rub > channel.value_max_rub:
            return False, 0
    # 货值无约束：score不加分

    return True, score


def estimate_shipping_cost(
    weight_kg: Decimal,
    price_rub: Optional[Decimal] = None,
    channels: Optional[list[LogisticsChannel]] = None,
    prefer_economy: bool = True,
) -> Optional[tuple[LogisticsChannel, Decimal]]:
    """
    根据产品重量和售价，估算最合适的 realFBS 物流运费(元)

    参数:
        weight_kg: 产品重量(kg)
        price_rub: 产品售价(卢布)，用于货值区间匹配
        channels: 预加载的渠道列表，如不传则自动加载
        prefer_economy: True=优先匹配经济渠道(陆运)，False=优先匹配标准渠道

    返回:
        (匹配的渠道, 运费元) 或 None(无匹配渠道)

    匹配策略:
        1. 优先选择同时满足重量+货值约束的渠道（匹配精度高）
        2. 同精度下优先经济渠道(陆运)
    """
    if channels is None:
        channels = load_logistics_table()

    matches: list[tuple[int, LogisticsChannel]] = []  # (score, channel)

    for ch in channels:
        ok, score = _channel_match_score(ch, weight_kg, price_rub)
        if ok:
            # 运输方式优先级加成: 陆运=2, 陆空联运=1, 空运=0
            if "陆运" in ch.transport_mode and "陆空" not in ch.transport_mode:
                mode_priority = 2
            elif "陆空" in ch.transport_mode:
                mode_priority = 1
            else:
                mode_priority = 0

            if prefer_economy:
                total_score = score * 10 + mode_priority
            else:
                total_score = score * 10  # 不区分运输方式

            matches.append((total_score, ch))

    if not matches:
        return None

    # 按总分降序排列，取最高分
    matches.sort(key=lambda x: x[0], reverse=True)
    ch = matches[0][1]
    shipping_cost = weight_kg * ch.per_kg_rate + ch.per_ticket_rate
    return ch, shipping_cost


# ── 预加载(模块级缓存) ──
_logistics_cache: Optional[list[LogisticsChannel]] = None


def get_cached_channels() -> list[LogisticsChannel]:
    global _logistics_cache
    if _logistics_cache is None:
        _logistics_cache = load_logistics_table()
    return _logistics_cache
