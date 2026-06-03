from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .config import settings


UNBRANDED_VALUES = {
    "",
    "无品牌",
    "未填写品牌",
    "无",
    "none",
    "no brand",
    "нет бренда",
    "без бренда",
}

RUB_CURRENCY_VALUES = {"rub", "rur", "₽", "руб"}
CNY_CURRENCY_VALUES = {"cny", "rmb", "cnh", "¥", "￥", "yuan"}


@dataclass(frozen=True)
class RangeRule:
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def check(self, value: Decimal | int | None, label: str, reasons: list[str]) -> None:
        if value is None:
            reasons.append(f"{label}缺失")
            return
        decimal_value = Decimal(str(value))
        if self.minimum is not None and decimal_value < self.minimum:
            reasons.append(f"{label}<{self.minimum}")
        if self.maximum is not None and decimal_value > self.maximum:
            reasons.append(f"{label}>{self.maximum}")


@dataclass(frozen=True)
class ProductSelectionRule:
    name: str
    require_unbranded: bool = False
    sold_count: RangeRule = field(default_factory=RangeRule)
    price: RangeRule = field(default_factory=RangeRule)
    weight_g: RangeRule = field(default_factory=RangeRule)
    create_days: RangeRule = field(default_factory=RangeRule)
    redemption_rate: RangeRule = field(default_factory=RangeRule)
    seller_offer_count: RangeRule = field(default_factory=RangeRule)
    required_sales_schema: str | None = None


@dataclass(frozen=True)
class ProductSelectionResult:
    matched: bool
    rule_name: str
    reasons: list[str]

    @property
    def summary(self) -> str:
        if self.matched:
            return f"命中规则: {self.rule_name}"
        return f"未命中规则: {self.rule_name}; " + "; ".join(self.reasons)


DEFAULT_SELECTION_RULE = ProductSelectionRule(
    name="0325 优质品",
    require_unbranded=True,
    sold_count=RangeRule(minimum=Decimal("1"), maximum=Decimal("65")),
    price=RangeRule(minimum=Decimal("20"), maximum=Decimal("800")),
    weight_g=RangeRule(maximum=Decimal("5000")),
    create_days=RangeRule(maximum=Decimal("180")),
    redemption_rate=RangeRule(maximum=Decimal("5")),
    seller_offer_count=RangeRule(maximum=Decimal("25")),
    required_sales_schema="FBS",
)


TOP_LIST_SEED_RULE = ProductSelectionRule(
    name="榜单种子扩展",
    require_unbranded=False,
    sold_count=RangeRule(minimum=Decimal("3"), maximum=Decimal("200")),
    price=RangeRule(),
    weight_g=RangeRule(maximum=Decimal("5000")),
    create_days=RangeRule(maximum=Decimal("200")),
    redemption_rate=RangeRule(maximum=Decimal("5")),
    seller_offer_count=RangeRule(maximum=Decimal("50")),
    required_sales_schema="FBS",
)


def evaluate_selection_rule(
    metric: dict[str, Any],
    product: dict[str, Any],
    seller_offer_count: int | None,
    rule: ProductSelectionRule = DEFAULT_SELECTION_RULE,
) -> ProductSelectionResult:
    reasons: list[str] = []

    brand = normalize_text(metric.get("brand") or product.get("brand"))
    if rule.require_unbranded and brand not in UNBRANDED_VALUES:
        reasons.append(f"品牌不是无品牌({metric.get('brand') or product.get('brand')})")

    rule.sold_count.check(metric.get("sold_count"), "月销量", reasons)
    price_cny = product_price_cny(product)
    rule.price.check(price_cny, "价格(CNY)", reasons)
    rule.weight_g.check(metric.get("custom_weight_g"), "重量(g)", reasons)
    rule.create_days.check(metric.get("create_days"), "上架天数", reasons)
    rule.redemption_rate.check(metric.get("nullable_redemption_rate"), "退货取消率", reasons)
    rule.seller_offer_count.check(seller_offer_count, "跟卖人数", reasons)

    if rule.required_sales_schema:
        sales_schema = normalize_text(metric.get("sales_schema"))
        tokens = {token.strip().upper() for token in sales_schema.split(",") if token.strip()}
        if rule.required_sales_schema.upper() not in tokens:
            reasons.append(f"发货模式不包含{rule.required_sales_schema}")

    return ProductSelectionResult(
        matched=not reasons,
        rule_name=rule.name,
        reasons=reasons,
    )


def evaluate_top_list_prefilter(
    item: dict[str, Any],
    rule: ProductSelectionRule = DEFAULT_SELECTION_RULE,
    today: date | None = None,
) -> ProductSelectionResult:
    today = today or date.today()
    reasons: list[str] = []

    brand = normalize_text(item.get("brand"))
    if rule.require_unbranded and brand not in UNBRANDED_VALUES:
        reasons.append(f"品牌不是无品牌({item.get('brand')})")

    rule.sold_count.check(item.get("sold_count"), "月销量", reasons)
    avg_price_cny = price_to_cny(item.get("avg_price"), "RUB")
    rule.price.check(avg_price_cny, "价格(CNY)", reasons)

    weight_value = item.get("weight")
    if weight_value not in (None, "", 0, "0", 0.0):
        rule.weight_g.check(weight_value, "重量(g)", reasons)

    create_value = item.get("nullable_create_date")
    create_days = None
    if create_value:
        if isinstance(create_value, datetime):
            create_days = (today - create_value.date()).days
        elif isinstance(create_value, date):
            create_days = (today - create_value).days
        else:
            try:
                parsed = datetime.strptime(str(create_value), "%Y-%m-%d").date()
                create_days = (today - parsed).days
            except ValueError:
                create_days = None
    if create_days is not None:
        rule.create_days.check(create_days, "上架天数", reasons)

    if rule.required_sales_schema:
        sales_schema = normalize_text(item.get("sales_schema"))
        tokens = {token.strip().upper() for token in sales_schema.split(",") if token.strip()}
        if rule.required_sales_schema.upper() not in tokens:
            reasons.append(f"发货模式不包含{rule.required_sales_schema}")

    return ProductSelectionResult(
        matched=not reasons,
        rule_name=rule.name,
        reasons=reasons,
    )


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def normalize_currency(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def price_to_cny(value: Any, currency: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    decimal_value = Decimal(str(value))
    normalized = normalize_currency(currency)
    if normalized in CNY_CURRENCY_VALUES:
        return decimal_value
    if normalized in RUB_CURRENCY_VALUES or normalized == "":
        return decimal_value * Decimal(str(settings.rub_to_cny_rate))
    return decimal_value


def product_price_cny(product: dict[str, Any]) -> Decimal | None:
    if product.get("price_cny") not in (None, ""):
        return Decimal(str(product.get("price_cny")))
    return price_to_cny(product.get("price"), product.get("currency"))
