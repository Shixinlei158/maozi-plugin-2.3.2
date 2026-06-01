"""
临界采购价格计算器

公式推导:
  售价 = 利润 + 运费×1.2 + 佣金 + 贴单费 + 损耗 + 采购成本

代入:
  利润   = 售价 × 0.15
  佣金   = 售价 × 0.15
  损耗   = 售价 × 0.03
  贴单费 = 0.5 元

得:
  采购成本 = 售价 - 售价×0.15 - 运费×1.2 - 售价×0.15 - 0.5 - 售价×0.03
  采购成本 = 售价 × (1 - 0.15 - 0.15 - 0.03) - 运费×1.2 - 0.5
  采购成本 = 售价 × 0.67 - 运费×1.2 - 0.5
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .config import settings
from .logistics import (
    LogisticsChannel,
    estimate_shipping_cost,
    get_cached_channels,
)


# ── 汇率转换 ──

def price_to_cny(price: Optional[Decimal], currency: Optional[str]) -> Optional[Decimal]:
    """将价格转换为人民币"""
    if price is None:
        return None
    currency = (currency or "").strip().lower()
    if currency in ("cny", "rmb", "cnh", "¥", "￥", "yuan"):
        return price
    # RUB 或其他未识别币种一律按卢布处理
    return price * settings.rub_to_cny_rate


# ── 临界采购价格 ──

@dataclass
class CriticalPriceResult:
    """临界价格计算结果"""
    sku: str
    title: str = ""

    # 输入
    price_raw: Optional[Decimal] = None  # 原始价格
    currency: Optional[str] = None  # 原始币种
    price_cny: Optional[Decimal] = None  # 转换后人民币售价
    weight_kg: Decimal = Decimal("0")  # 重量(kg)

    # 物流
    matched_channel: Optional[LogisticsChannel] = None
    shipping_cost_cny: Decimal = Decimal("0")  # 基础运费(元)
    shipping_safe_cny: Decimal = Decimal("0")  # 安全运费=基础×1.2(元)

    # 成本拆解
    profit_cny: Decimal = Decimal("0")  # 利润
    commission_cny: Decimal = Decimal("0")  # 佣金
    loss_cny: Decimal = Decimal("0")  # 损耗

    # 结果
    critical_procurement_cny: Optional[Decimal] = None  # 临界采购价格(元)
    gross_margin_pct: Optional[Decimal] = None  # 毛利率(%)
    is_viable: bool = False  # 是否可行(临界价>0)

    error: str = ""  # 错误信息


def calc_critical_procurement_price(
    price_rub: Optional[Decimal],
    weight_kg: Decimal,
    currency: Optional[str] = None,
    channels: Optional[list[LogisticsChannel]] = None,
    sku: str = "",
    title: str = "",
) -> CriticalPriceResult:
    """
    计算临界采购价格（不亏本的最高采购成本）

    参数:
        price_rub: 产品售价（若currency=RUB则为卢布）
        weight_kg: 产品重量(kg)
        currency: 币种，默认RUB
        channels: 预加载的物流渠道列表
        sku: 产品SKU
        title: 产品标题

    返回:
        CriticalPriceResult 包含完整计算结果
    """
    result = CriticalPriceResult(sku=sku, title=title)

    # 1. 价格转换
    currency = currency or "RUB"
    result.price_raw = price_rub
    result.currency = currency
    result.price_cny = price_to_cny(price_rub, currency)
    result.weight_kg = weight_kg

    if result.price_cny is None or result.price_cny <= 0:
        result.error = "售价无效或为零"
        return result

    if weight_kg <= 0:
        result.error = "重量无效或为零"
        return result

    # 2. 物流运费估算
    channels = channels or get_cached_channels()
    ship_result = estimate_shipping_cost(
        weight_kg=weight_kg,
        price_rub=price_rub if currency.upper() == "RUB" else None,
        channels=channels,
    )

    if ship_result is None:
        result.error = "无匹配的物流渠道"
        return result

    result.matched_channel, result.shipping_cost_cny = ship_result
    result.shipping_safe_cny = result.shipping_cost_cny * settings.shipping_safety_factor

    # 3. 成本拆解
    price = result.price_cny
    result.profit_cny = price * settings.profit_margin
    result.commission_cny = price * settings.commission_rate
    result.loss_cny = price * settings.loss_rate

    # 4. 临界采购价格 = 售价×0.67 - 运费×1.2 - 0.5
    revenue_after_deductions = (
        price
        - result.profit_cny
        - result.commission_cny
        - result.loss_cny
    )
    result.critical_procurement_cny = (
        revenue_after_deductions
        - result.shipping_safe_cny
        - settings.sticker_fee_cny
    ).quantize(Decimal("0.01"), ROUND_HALF_UP)

    # 5. 毛利率
    if price > 0 and result.critical_procurement_cny > 0:
        result.gross_margin_pct = (
            (price - result.critical_procurement_cny) / price * 100
        ).quantize(Decimal("0.01"), ROUND_HALF_UP)

    result.is_viable = result.critical_procurement_cny > 0

    return result


def product_critical_price(
    product: dict,
    channels: Optional[list[LogisticsChannel]] = None,
) -> CriticalPriceResult:
    """
    从 sku_products 字典记录中直接计算临界采购价格。

    产品字典需包含:
        - sku: str
        - price / card_price: Decimal 售价
        - currency: str 币种
        - custom_weight_g: Decimal 重量(克)

    价格优先使用 card_price(卡片价)，无则用 price。
    """
    sku = str(product.get("sku", ""))
    title = str(product.get("title", ""))

    # 价格：card_price 优先（实际售价），其次 price
    card_price = product.get("card_price")
    price = card_price if card_price is not None else product.get("price")
    if isinstance(price, (int, float)):
        price = Decimal(str(price))

    currency = str(product.get("currency") or "RUB")

    # 重量：克 → 千克
    weight_g = product.get("custom_weight_g")
    if weight_g is not None:
        if isinstance(weight_g, (int, float)):
            weight_g = Decimal(str(weight_g))
        weight_kg = weight_g / Decimal("1000")
    else:
        weight_kg = Decimal("0")

    return calc_critical_procurement_price(
        price_rub=price,
        weight_kg=weight_kg,
        currency=currency,
        channels=channels,
        sku=sku,
        title=title,
    )
