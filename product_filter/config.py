"""
产品筛选临界价格测算 — 可调参数配置

所有百分比参数以小数形式表示，例如 15% → 0.15
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import os


@dataclass(frozen=True)
class ProductFilterSettings:
    # ── 利润/成本拆解参数 ──
    profit_margin: Decimal = Decimal("0.15")  # 利润率 = 售价 * 0.15
    commission_rate: Decimal = Decimal("0.15")  # 佣金率 = 售价 * 0.15
    loss_rate: Decimal = Decimal("0.03")  # 损耗率 = 售价 * 0.03
    sticker_fee_cny: Decimal = Decimal("0.5")  # 贴单费(元)/件
    shipping_safety_factor: Decimal = Decimal("1.2")  # 运费安全系数(乘以1.2)

    # ── 汇率 ──
    # 保守汇率：正常汇率(0.0912)下浮5%，确保不会高估人民币收入
    rub_to_cny_rate: Decimal = Decimal(os.getenv("RUB_TO_CNY_RATE_SAFE", "0.08664"))

    # ── 物流费率表路径 ──
    logistics_excel_path: str = str(
        Path(__file__).resolve().parent.parent
        / "GUOO产品资费测算表【2026.4.15更新】(4).xlsx"
    )
    logistics_sheet_name: str = "GUOO realFBS资费试算表"

    # ── 数据库连接（与主项目 ozon_pipeline 保持一致） ──
    db_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    db_user: str = os.getenv("MYSQL_USER", "root")
    db_password: str = os.getenv("MYSQL_PASSWORD", "")
    db_name: str = os.getenv("MYSQL_DATABASE", "ozon_selection")


settings = ProductFilterSettings()
