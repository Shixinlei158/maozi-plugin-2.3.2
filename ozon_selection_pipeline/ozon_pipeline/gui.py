from __future__ import annotations

import json
import os
import queue
import random
import sys
import threading
import tkinter as tk
import traceback
from argparse import Namespace
from datetime import datetime, timedelta
from tkinter import Frame, Label, Button, Entry, ttk, messagebox, scrolledtext, BooleanVar, StringVar, Toplevel
from typing import Any

from .browser_ozon import BrowserOzonClient
from .config import settings, ROOT_DIR
from .feishu import notify_collection_failed

GUI_CONFIG_FILE = ROOT_DIR / "gui_config.json"
GUI_FIRST_RUN_FILE = ROOT_DIR / ".gui_first_run"

class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self.show_tip)
        widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x, y, cx, cy = self.widget.bbox("insert")
        x = x + self.widget.winfo_rootx() + 25
        y = y + cy + self.widget.winfo_rooty() + 25
        self.tip_window = tw = Toplevel(self.widget)
        tw.wm_overrideredirect(1)
        tw.wm_geometry("+%d+%d" % (x, y))
        label = Label(tw, text=self.text, justify="left",
                      background="#ffffe0", relief="solid", borderwidth=1,
                      font=("Microsoft YaHei", "8", "normal"))
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        tw = self.tip_window
        self.tip_window = None
        if tw:
            tw.destroy()

class _GuiSummaryLogger:
    def __init__(self, queue: queue.Queue[str]):
        self._queue = queue
        self._buffer = ""
        self._start_time = datetime.now()
        self._set_next_interval()
        self._seller_count = 0
        self._raw_skus = 0
        self._processed_skus = 0
        self._qualified_skus = 0
        self._rejected_skus = 0
        self._deferred_skus = 0
        self._skipped_skus = 0
        self._offers = 0
        self._last_seller = ""
        self._queue_length = 0

    def _set_next_interval(self):
        interval_minutes = random.uniform(0.5, 1.0)
        self._next_summary_at = datetime.now() + timedelta(minutes=interval_minutes)

    def write(self, text: str) -> None:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._handle_line(line.rstrip("\r"))

    def flush(self) -> None:
        if self._buffer:
            self._handle_line(self._buffer.rstrip("\r\n"))
            self._buffer = ""

    def close(self) -> None:
        self.flush()

    def _handle_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        self._update_counters(stripped)
        self._queue.put(stripped + "\n")
        now = datetime.now()
        if now >= self._next_summary_at:
            self.emit_summary(now)

    def _update_counters(self, line: str) -> None:
        if line.startswith("crawl seller:"):
            # 不在这里增加已完成卖家，仅记录发现的SKU数
            self._last_seller = line.split("|", 1)[0].replace("crawl seller:", "").strip()
            self._raw_skus += self._int_after(line, "items=")
            return
        if line.startswith("expand-network round") and "due:" in line:
            self._queue_length = self._int_after(line, "fetched=")
            return
        if line.startswith("seller summary:"):
            self._seller_count += 1  # 卖家完全处理结束后才 +1
            self._processed_skus += self._int_after(line, "skus=")
            self._qualified_skus += self._int_after(line, "qualified=")
            self._rejected_skus += self._int_after(line, "rejected=")
            self._deferred_skus += self._int_after(line, "deferred=")
            self._skipped_skus += self._int_after(line, "skipped=")
            self._offers += self._int_after(line, "offers=")
            return
        if line.startswith("expand-network final summary:"):
            self.emit_summary(datetime.now(), force=True)

    def emit_summary(self, now: datetime, force: bool = False) -> None:
        if not force and self._seller_count == 0 and self._processed_skus == 0:
            self._set_next_interval()
            return
        
        runtime = now - self._start_time
        hours, remainder = divmod(int(runtime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        runtime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        parts = [
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 定时状态报告:",
            f"已完成卖家={self._seller_count}",
            f"当前运行时长={runtime_str}",
            f"待处理队列长度={self._queue_length}",
            f"达标SKU={self._qualified_skus}",
            f"淘汰SKU={self._rejected_skus}",
        ]
        self._queue.put("\n" + " | ".join(parts) + "\n\n")
        self._set_next_interval()

    @staticmethod
    def _int_after(line: str, key: str) -> int:
        start = line.find(key)
        if start < 0:
            return 0
        start += len(key)
        digits = []
        for char in line[start:]:
            if char == " " and not digits:
                continue
            if char.isdigit():
                digits.append(char)
                continue
            break
        return int("".join(digits) or "0")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Ozon 采集控制台 (v2.3.2 专业版)")
        self.root.geometry("1100x900")
        self.root.minsize(960, 800)
        self.root.configure(bg="#f5f6fa")

        self._stop_flag = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr
        self._logger = _GuiSummaryLogger(self._log_queue)

        self._param_widgets: dict[str, Any] = {}
        self._param_vars: dict[str, tk.Variable] = {}
        self._mode_var = tk.StringVar(value="expand-seller-backlog")
        self._runtime_stats: dict[str, str] = {}

        self._build_ui()
        self._reset_runtime_stats()
        self._load_config()
        self._update_linkages()
        self._poll_status()
        self._start_log_poller()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
        self.root.after(1000, self._check_first_run)

    def _check_first_run(self):
        if not GUI_FIRST_RUN_FILE.exists():
            self._show_novice_guide()
            try:
                GUI_FIRST_RUN_FILE.touch()
            except Exception:
                pass

    def _show_novice_guide(self):
        guide = Toplevel(self.root)
        guide.title("新手使用引导")
        guide.geometry("500x400")
        guide.resizable(False, False)
        guide.transient(self.root)
        guide.grab_set()
        
        Frame(guide, height=20, bg="#f5f6fa").pack()
        Label(guide, text="欢迎使用 Ozon 采集系统！", font=("Microsoft YaHei", 14, "bold")).pack(pady=10)
        
        steps = [
            "1. 顶部状态栏：实时检测 CDP 浏览器和数据库连接状态。",
            "2. 采集模式：推荐使用'卖家列表循环扩充'，收益最高。",
            "3. 参数配置：鼠标悬停在配置项名称上可查看详细用途及示例。",
            "4. 启动采集：先'启动浏览器'，确认 CDP 已连接后再点击'开始采集'。",
            "5. 定时报告：系统每 6-9 分钟会自动输出运行统计到日志区。"
        ]
        
        f = Frame(guide, padx=30)
        f.pack(fill="both", expand=True)
        for step in steps:
            Label(f, text=step, font=("Microsoft YaHei", 10), justify="left", wraplength=440, pady=5).pack(anchor="w")
            
        Button(guide, text="我知道了", command=guide.destroy, bg="#3498db", fg="white", 
               font=("Microsoft YaHei", 10, "bold"), width=15, pady=5).pack(pady=20)

    def _build_ui(self):
        try:
            self._build_ui_inner()
        except Exception:
            traceback.print_exc()

    def _build_ui_inner(self):
        # 顶部状态栏
        info_bar = Label(
            self.root,
            text="Ozon Selection Pipeline — 智能采集调度中心",
            bg="#2c3e50",
            fg="white",
            font=("Microsoft YaHei", 11, "bold"),
            pady=6,
        )
        info_bar.pack(fill="x")

        dashboard = Frame(self.root, bg="#ffffff", padx=12, pady=8, relief="ridge", bd=1)
        dashboard.pack(fill="x", padx=8, pady=(8, 4))
        Label(dashboard, text="运行状态", bg="#ffffff", font=("Microsoft YaHei", 10, "bold")).grid(
            row=0, column=0, columnspan=5, sticky="w", pady=(0, 4)
        )

        self._cdp_label = Label(dashboard, text="CDP: 检测中...", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._cdp_label.grid(row=1, column=0, padx=(0, 10), sticky="w")
        self._db_label = Label(dashboard, text="DB:  检测中...", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._db_label.grid(row=1, column=1, padx=(0, 10), sticky="w")
        self._plugin_label = Label(dashboard, text="插件: 检测中...", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._plugin_label.grid(row=1, column=2, padx=(0, 10), sticky="w")
        self._seller_label = Label(dashboard, text="卖家: 检测中...", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._seller_label.grid(row=1, column=3, sticky="w")
        self._db_profile_var = tk.StringVar(value="ts-lx")
        db_profile_frame = Frame(dashboard, bg="#ffffff")
        db_profile_frame.grid(row=1, column=4, sticky="w")
        Label(db_profile_frame, text="DB:", bg="#ffffff", font=("Microsoft YaHei", 9)).pack(side="left")
        db_combo = ttk.Combobox(db_profile_frame, textvariable=self._db_profile_var,
                                values=["local", "ts-lx", "frp-lx"],
                                state="readonly", width=8, font=("Microsoft YaHei", 9))
        db_combo.pack(side="left", padx=(2, 4))
        db_combo.bind("<<ComboboxSelected>>", self._on_db_profile_changed)
        self._sync_btn = Button(db_profile_frame, text="同步", bg="#95a5a6", fg="white",
                               font=("Microsoft YaHei", 8), width=4, padx=2, pady=0,
                               command=self._sync_db_structure, relief="flat")
        self._sync_btn.pack(side="left")
        self._db_status_label = Label(db_profile_frame, text="", bg="#ffffff", font=("Microsoft YaHei", 8), fg="#27ae60")
        self._db_status_label.pack(side="left", padx=(4, 0))
        self._mode_label = Label(dashboard, text="模式: 待机", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._mode_label.grid(row=2, column=0, padx=(0, 10), sticky="w")
        self._phase_label = Label(dashboard, text="阶段: 未开始", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._phase_label.grid(row=2, column=1, padx=(0, 10), sticky="w")
        self._progress_label = Label(dashboard, text="进度: 暂无", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._progress_label.grid(row=2, column=2, padx=(0, 10), sticky="w")
        self._issue_label = Label(dashboard, text="异常: 暂无", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._issue_label.grid(row=2, column=3, sticky="w")
        self._current_seller_label = Label(
            dashboard,
            text="当前卖家: 暂无",
            bg="#ffffff",
            font=("Microsoft YaHei", 9),
            anchor="w",
            justify="left",
            wraplength=980,
        )
        self._current_seller_label.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        # 核心控制区
        control_panel = Frame(self.root, bg="#ffffff", padx=12, pady=8, relief="ridge", bd=1)
        control_panel.pack(fill="x", padx=8, pady=4)

        # 模式选择
        Label(control_panel, text="采集模式", bg="#ffffff", font=("Microsoft YaHei", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        mode_frame = Frame(control_panel, bg="#ffffff")
        mode_frame.grid(row=1, column=0, sticky="w", pady=(0, 6))
        modes = [
            ("卖家列表循环扩充 (推荐)", "expand-seller-backlog"),
            ("榜单采集网络", "crawl-top-list-network"),
            ("种子池处理", "expand-seed-pool-network"),
            ("多类目采集网络", "multi-category-network"),
        ]
        for idx, (text, value) in enumerate(modes):
            ttk.Radiobutton(
                mode_frame,
                text=text,
                variable=self._mode_var,
                value=value,
                command=self._update_linkages
            ).grid(row=0, column=idx, padx=(0, 14))

        # 参数配置 Notebook
        self._notebook = ttk.Notebook(control_panel)
        self._notebook.grid(row=2, column=0, sticky="ew", pady=(6, 4))

        tab_core = Frame(self._notebook, bg="#ffffff", padx=8, pady=8)
        tab_conc = Frame(self._notebook, bg="#ffffff", padx=8, pady=8)
        tab_adv = Frame(self._notebook, bg="#ffffff", padx=8, pady=8)

        self._notebook.add(tab_core, text="核心调度参数")
        self._notebook.add(tab_conc, text="并发与频率控制")
        self._notebook.add(tab_adv, text="模式专属配置")

        # Tab 1: 核心调度
        self._add_param(tab_core, 0, 0, "每轮处理上限 (process_limit)", "process_limit", "0", "int", min_val=0, max_val=10000,
                       tooltip="每轮任务最多处理的SKU或卖家数量。0=不限制，一次处理全部到期种子")
        self._add_param(tab_core, 0, 1, "最大爬取深度 (max_depth)", "max_depth", "-1", "int", min_val=-1, max_val=100,
                       tooltip="-1=无限递归，所有跟卖卖家都会被爬。谨慎使用")
        self._add_param(tab_core, 1, 0, "最大卖家数 (max_sellers)", "max_sellers", "0", "int", min_val=0, max_val=100000, 
                       tooltip="本轮最多访问的卖家数量。0表示不限制；生产长跑建议：0")
        self._add_param(tab_core, 1, 1, "单店SKU上限 (sku_limit)", "sku_limit", "0", "int", min_val=0, max_val=100000, 
                       tooltip="从每个卖家主页提取的SKU最大数量。0表示全部提取。示例：200")
        self._add_param(tab_core, 2, 0, "卖家页最大滚动 (max_scrolls)", "max_scrolls", "8", "int", min_val=0, max_val=50,
                       tooltip="若API失效回退到DOM模式时，页面的滚动次数。示例：8")

        # Tab 2: 并发控制
        self._add_param(tab_conc, 0, 0, "SKU3 批量大小 (batch_size)", "batch_size", str(settings.top_list_sku3_batch_size), "int", min_val=1, max_val=100,
                       tooltip="单次请求sku3接口的SKU数量。建议：40")
        self._add_param(tab_conc, 0, 1, "SKU3 批内并发 (concurrency)", "concurrency", str(settings.top_list_sku3_batch_concurrency), "int", min_val=1, max_val=50,
                       tooltip="批次内部同时发起的fetch请求数。建议：12")
        self._add_param(tab_conc, 1, 0, "批次间延迟ms (chunk_delay_ms)", "chunk_delay_ms", str(settings.top_list_sku3_batch_chunk_delay_ms), "int", min_val=0, max_val=10000,
                       tooltip="每组批量请求之间的休眠时间。建议：500")
        self._add_param(tab_conc, 2, 0, "卖家SKU线程数 (seller_sku_workers)", "seller_sku_workers", str(settings.seller_sku_workers), "int", min_val=1, max_val=32,
                       tooltip="并行处理卖家主页SKU的本地线程数。示例：4")
        self._add_param(tab_conc, 2, 1, "卖家页并发数 (seller_page_workers)", "seller_page_workers", str(settings.seller_page_workers), "int", min_val=1, max_val=8,
                       tooltip="同时爬取卖家主页的数量。增大可提速，但对内存和CDP管道有压力。建议：2-3")
        self._add_param(tab_conc, 3, 0, "种子SKU线程数 (seed_sku_workers)", "seed_sku_workers", str(settings.seed_sku_workers), "int", min_val=1, max_val=32,
                       tooltip="并行处理种子池SKU的本地线程数。示例：8")

        # Tab 3: 高级与特定模式
        # 种子池组
        lf_seed = tk.LabelFrame(tab_adv, text="种子池专属配置", bg="#ffffff", padx=8, pady=8)
        lf_seed.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self._add_param(lf_seed, 0, 0, "强制重试失败项", "retry_failed_now", False, "bool", 
                       tooltip="勾选后将重试状态为failed的任务")
        self._add_param(lf_seed, 0, 1, "强制重试暂缓项", "retry_deferred_now", False, "bool",
                       tooltip="勾选后将重试状态为deferred的任务")
        self._add_param(lf_seed, 1, 0, "种子来源过滤", "source_type", "top_list", "str",
                       tooltip="过滤特定来源的种子。可选：top_list, manual")
        self._add_param(lf_seed, 1, 1, "强制重试淘汰项", "retry_rejected_now", False, "bool", 
                       tooltip="勾选后将重试状态为rejected/done的任务")

        # 榜单组 — 按毛子ERP实际页面字段顺序排列
        lf_top = tk.LabelFrame(tab_adv, text="榜单采集专属配置（留空=不过滤，顺序与网页一致）", bg="#ffffff", padx=8, pady=8)
        lf_top.grid(row=1, column=0, sticky="ew", padx=4, pady=4)

        def _add_section_label(parent, row, text):
            lbl = Label(parent, text=text, bg="#ffffff", font=("Microsoft YaHei", 9, "bold"), fg="#3498db")
            lbl.grid(row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 2))

        _add_section_label(lf_top, 0, "基础设置")
        self._add_param(lf_top, 1, 0, "榜单类型", "main_type", "hot", "str",
                       tooltip="毛子榜单分类：hot(热销), new(新品), potential(潜力)")
        self._add_param(lf_top, 1, 1, "每页数量", "page_size", "50", "int", min_val=1, max_val=100,
                       tooltip="榜单单页请求条数")
        self._add_param(lf_top, 1, 2, "起始页码", "page_from", "1", "int", min_val=1, max_val=1000,
                       tooltip="从第几页开始采集")
        self._add_param(lf_top, 2, 0, "结束页码", "page_to", "100", "int", min_val=1, max_val=1000,
                       tooltip="采集到第几页停止")
        self._add_param(lf_top, 2, 1, "类目1", "category1", "", "str",
                       tooltip="一级类目名称或ID")
        self._add_param(lf_top, 2, 2, "类目2", "category2", "", "str",
                       tooltip="二级类目名称或ID")
        self._add_param(lf_top, 3, 0, "类目3", "category3", "", "str",
                       tooltip="三级类目名称或ID")

        # 榜单过滤 — 完全按网页顺序：商品名称 → SKU → 月销量 → 日均销量 → 均价 → 月销售额环比 → 月销售额 → 商品卡加购率 → 搜索加购率 → 发货模式 → 重量 → 交货时间 → 上架时间
        _add_section_label(lf_top, 4, "榜单过滤（按网页顺序）")
        self._add_param(lf_top, 5, 0, "商品名称", "name", "", "str",
                       tooltip="按商品名称模糊搜索")
        self._add_param(lf_top, 5, 1, "SKU", "sku", "", "str",
                       tooltip="按SKU编号精确搜索")
        self._add_param(lf_top, 5, 2, "月销量≥", "sales_min", "3", "str",
                       tooltip="月销量最小值。默认：3")
        self._add_param(lf_top, 6, 0, "月销量≤", "sales_max", "200", "str",
                       tooltip="月销量最大值。默认：200")
        self._add_param(lf_top, 6, 1, "日均销量≥", "day_sales_min", "", "str",
                       tooltip="日均销量最小值")
        self._add_param(lf_top, 6, 2, "日均销量≤", "day_sales_max", "", "str",
                       tooltip="日均销量最大值")
        self._add_param(lf_top, 7, 0, "均价≥(₽)", "avg_price_min", "500", "str",
                       tooltip="平均价格最小值(卢布)。默认：500")
        self._add_param(lf_top, 7, 1, "均价≤(₽)", "avg_price_max", "10000", "str",
                       tooltip="平均价格最大值(卢布)。默认：10000")
        self._add_param(lf_top, 7, 2, "月销售额环比≥(%)", "sales_dynamics_min", "", "str",
                       tooltip="月度销售额环比最小值(百分比)")
        self._add_param(lf_top, 8, 0, "月销售额环比≤(%)", "sales_dynamics_max", "", "str",
                       tooltip="月度销售额环比最大值(百分比)")
        self._add_param(lf_top, 8, 1, "月销售额≥(₽)", "sold_sum_min", "", "str",
                       tooltip="月销售额最小值(卢布)。示例：10000")
        self._add_param(lf_top, 8, 2, "月销售额≤(₽)", "sold_sum_max", "", "str",
                       tooltip="月销售额最大值(卢布)")
        self._add_param(lf_top, 9, 0, "商品卡加购率≥(%)", "conv_to_cart_pdp_min", "", "str",
                       tooltip="商品详情页加购转化率最小值(百分比)")
        self._add_param(lf_top, 9, 1, "商品卡加购率≤(%)", "conv_to_cart_pdp_max", "", "str",
                       tooltip="商品详情页加购转化率最大值(百分比)")
        self._add_param(lf_top, 9, 2, "搜索加购率≥(%)", "conv_to_cart_search_min", "", "str",
                       tooltip="搜索和目录加购转化率最小值(百分比)")
        self._add_param(lf_top, 10, 0, "搜索加购率≤(%)", "conv_to_cart_search_max", "", "str",
                       tooltip="搜索和目录加购转化率最大值(百分比)")
        self._add_param(lf_top, 10, 1, "发货模式", "sales_schema", "FBS", "str",
                       tooltip="发货模式过滤：FBS, FBP, rFBS 等。默认：FBS")
        self._add_param(lf_top, 10, 2, "重量≥(g)", "weight_min", "", "str",
                       tooltip="商品重量最小值(克)。示例：100")
        self._add_param(lf_top, 11, 0, "重量≤(g)", "weight_max", "5000", "str",
                       tooltip="商品重量最大值(克)。默认：5000")
        self._add_param(lf_top, 11, 1, "交货天数≥(天)", "avg_delivery_days_min", "", "str",
                       tooltip="平均交货天数最小值")
        self._add_param(lf_top, 11, 2, "交货天数≤(天)", "avg_delivery_days_max", "", "str",
                       tooltip="平均交货天数最大值")

        _add_section_label(lf_top, 12, "其他")
        self._add_param(lf_top, 13, 0, "上架起始", "create_date_from", "", "str",
                       tooltip="商品上架起始日期，格式: YYYY-MM-DD")
        self._add_param(lf_top, 13, 1, "上架截止", "create_date_to", "", "str",
                       tooltip="商品上架截止日期，格式: YYYY-MM-DD")
        self._add_param(lf_top, 13, 2, "排序字段", "sort_by", "", "str",
                       tooltip="排序依据字段。常用：sold_count(月销), avg_price(均价), create_date(上架时间)")
        self._add_param(lf_top, 14, 0, "排序方向", "sort_order", "", "str",
                       tooltip="排序方向：asc(升序), desc(降序)")

        # 多类目组
        lf_mcat = tk.LabelFrame(tab_adv, text="多类目采集专属配置", bg="#ffffff", padx=8, pady=8)
        lf_mcat.grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        self._add_param(lf_mcat, 0, 0, "类目层级", "category_level", "1", "str",
                       tooltip="遍历哪一层的类目：1(一级,26个), 2(二级,393个), 3(三级,1429个), all(全部)")
        self._add_param(lf_mcat, 0, 1, "每类目页数", "pages_per_category", "5", "int", min_val=1, max_val=100,
                       tooltip="每个类目拉取多少页。全部类目页数=类目数×每类目页数")

        # 按钮区
        btn_frame = Frame(control_panel, bg="#ffffff")
        btn_frame.grid(row=3, column=0, sticky="w", pady=(12, 0))

        self._start_browser_btn = Button(
            btn_frame, text="启动浏览器", bg="#3498db", fg="white", font=("Microsoft YaHei", 10, "bold"),
            width=12, padx=8, pady=4, command=self._launch_browser, relief="flat"
        )
        self._start_browser_btn.pack(side="left", padx=(0, 8))

        self._restart_browser_btn = Button(
            btn_frame, text="重启浏览器", bg="#e67e22", fg="white", font=("Microsoft YaHei", 10, "bold"),
            width=12, padx=8, pady=4, command=self._restart_browser, relief="flat"
        )
        self._restart_browser_btn.pack(side="left", padx=(0, 8))

        self._start_btn = Button(
            btn_frame, text="▶ 开始采集", bg="#27ae60", fg="white", font=("Microsoft YaHei", 10, "bold"),
            width=12, padx=8, pady=4, command=self._start_collection, relief="flat"
        )
        self._start_btn.pack(side="left", padx=(0, 8))

        self._refresh_btn = Button(
            btn_frame, text="↻ 刷新状态", bg="#3498db", fg="white", font=("Microsoft YaHei", 10),
            width=10, padx=8, pady=4, command=self._manual_refresh_status, relief="flat"
        )
        self._refresh_btn.pack(side="left", padx=(0, 8))

        self._stop_btn = Button(
            btn_frame, text="■ 停止采集", bg="#e74c3c", fg="white", font=("Microsoft YaHei", 10, "bold"),
            width=12, padx=8, pady=4, command=self._stop_collection, relief="flat", state="disabled"
        )
        self._stop_btn.pack(side="left", padx=(0, 8))

        self._save_btn = Button(
            btn_frame, text="暂存配置", bg="#f39c12", fg="white", font=("Microsoft YaHei", 9),
            padx=8, pady=4, command=self._save_config, relief="flat"
        )
        self._save_btn.pack(side="left", padx=(0, 8))

        self._reset_btn = Button(
            btn_frame, text="恢复默认", bg="#95a5a6", fg="white", font=("Microsoft YaHei", 9),
            padx=8, pady=4, command=self._reset_config, relief="flat"
        )
        self._reset_btn.pack(side="left", padx=(0, 8))

        self._clear_btn = Button(
            btn_frame, text="清空日志", bg="#95a5a6", fg="white", font=("Microsoft YaHei", 9),
            padx=8, pady=4, command=self._clear_log, relief="flat"
        )
        self._clear_btn.pack(side="left")

        # 日志区
        log_frame = Frame(self.root, bg="#ffffff", padx=12, pady=8, relief="ridge", bd=1)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        Label(log_frame, text="运行日志", bg="#ffffff", font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=(0, 4))

        self._log_area = scrolledtext.ScrolledText(
            log_frame, wrap="word", font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white", state="normal", height=20
        )
        self._log_area.pack(fill="both", expand=True)
        self._log_area.configure(state="disabled")
        self._log_area.tag_configure("error", foreground="#ff6b6b")
        self._log_area.tag_configure("warning", foreground="#ffd166")
        self._log_area.tag_configure("summary", foreground="#4cc9f0")
        self._log_area.tag_configure("success", foreground="#72efdd")
        self._log_area.tag_configure("progress", foreground="#a29bfe")

    def _add_param(self, parent: Frame, row: int, col: int, label: str, key: str, default: Any, ptype: str, min_val=None, max_val=None, tooltip=""):
        frame = Frame(parent, bg="#ffffff")
        frame.grid(row=row, column=col, sticky="w", padx=10, pady=4)
        
        lbl_text = label
        if tooltip:
            lbl_text += " (?)"
            
        lbl = Label(frame, text=lbl_text, bg="#ffffff", font=("Microsoft YaHei", 9), cursor="question_arrow")
        lbl.pack(side="left", padx=(0, 4))
        
        if tooltip:
            ToolTip(lbl, tooltip)
        
        if ptype == "bool":
            var = BooleanVar(value=bool(default))
            ent = ttk.Checkbutton(frame, variable=var)
            ent.pack(side="left")
        else:
            var = StringVar(value=str(default))
            ent = Entry(frame, textvariable=var, width=12, font=("Consolas", 9), relief="sunken", bd=1)
            ent.pack(side="left")
            
            # Save validation rules
            ent.validation_rules = {"ptype": ptype, "min": min_val, "max": max_val, "label": label}

        self._param_widgets[key] = ent
        self._param_vars[key] = var

    def _manual_refresh_status(self):
        if self._logger:
            self._logger.emit_summary(datetime.now(), force=True)
            self._append_log(">>> 已手动触发采集状态刷新\n")
        else:
            self._append_log(">>> 采集未运行，无法刷新状态\n")

    def _update_linkages(self):
        mode = self._mode_var.get()
        # Enable/Disable based on mode
        is_seed = (mode == "expand-seed-pool-network")
        is_top = (mode == "crawl-top-list-network")
        is_mcat = (mode == "multi-category-network")
        
        # 种子专属
        for key in ["retry_failed_now", "retry_deferred_now", "retry_rejected_now", "source_type"]:
            state = "normal" if is_seed else "disabled"
            if isinstance(self._param_widgets[key], ttk.Checkbutton):
                if state == "disabled":
                    self._param_widgets[key].state(['disabled'])
                else:
                    self._param_widgets[key].state(['!disabled'])
            else:
                self._param_widgets[key].configure(state=state)
                
        # 榜单专属（榜单采集和多类目采集共用）
        for key in [
            "main_type", "page_size", "page_from", "page_to",
            "sku", "name", "category1", "category2", "category3",
            "sales_min", "sales_max", "sold_sum_min", "sold_sum_max",
            "day_sales_min", "day_sales_max",
            "avg_price_min", "avg_price_max",
            "conv_to_cart_pdp_min", "conv_to_cart_pdp_max",
            "conv_to_cart_search_min", "conv_to_cart_search_max",
            "sales_dynamics_min", "sales_dynamics_max",
            "sales_schema",
            "weight_min", "weight_max",
            "avg_delivery_days_min", "avg_delivery_days_max",
            "create_date_from", "create_date_to",
            "sort_by", "sort_order",
        ]:
            state = "normal" if (is_top or is_mcat) else "disabled"
            self._param_widgets[key].configure(state=state)

        # 多类目专属
        for key in ["category_level", "pages_per_category"]:
            state = "normal" if is_mcat else "disabled"
            self._param_widgets[key].configure(state=state)

    def _validate_params(self) -> dict[str, Any]:
        params = {}
        for key, widget in self._param_widgets.items():
            if widget.cget("state") == "disabled" or (isinstance(widget, ttk.Checkbutton) and 'disabled' in widget.state()):
                continue
            
            var = self._param_vars[key]
            val = var.get()
            
            if isinstance(widget, ttk.Checkbutton):
                params[key] = bool(val)
                continue
                
            rules = getattr(widget, "validation_rules", {})
            ptype = rules.get("ptype", "str")
            label = rules.get("label", key)
            
            if ptype == "int":
                try:
                    int_val = int(val)
                except ValueError:
                    raise ValueError(f"参数 [{label}] 必须是整数！")
                
                min_val = rules.get("min")
                max_val = rules.get("max")
                if min_val is not None and int_val < min_val:
                    raise ValueError(f"参数 [{label}] 的值不能小于 {min_val}！")
                if max_val is not None and int_val > max_val:
                    raise ValueError(f"参数 [{label}] 的值不能大于 {max_val}！")
                params[key] = int_val
            else:
                params[key] = str(val)
                
        return params

    def _save_config(self):
        try:
            params = {k: v.get() for k, v in self._param_vars.items()}
            params["_mode"] = self._mode_var.get()
            with open(GUI_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(params, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("成功", "配置已成功暂存！下次启动将自动加载。")
        except Exception as e:
            messagebox.showerror("保存失败", f"保存配置失败: {e}")

    def _load_config(self):
        if not GUI_CONFIG_FILE.exists():
            return
        try:
            with open(GUI_CONFIG_FILE, "r", encoding="utf-8") as f:
                params = json.load(f)
            
            if "_mode" in params:
                self._mode_var.set(params.pop("_mode"))
                
            for k, v in params.items():
                if k in self._param_vars:
                    self._param_vars[k].set(v)
        except Exception:
            pass

    def _reset_config(self):
        if not messagebox.askyesno("确认", "确定要恢复所有参数到默认值吗？"):
            return
            
        defaults = {
            "process_limit": "0",
            "max_depth": "-1",
            "max_sellers": "0",
            "sku_limit": "0",
            "max_scrolls": "8",
            "batch_size": str(settings.top_list_sku3_batch_size),
            "concurrency": str(settings.top_list_sku3_batch_concurrency),
            "chunk_delay_ms": str(settings.top_list_sku3_batch_chunk_delay_ms),
            "seller_sku_workers": str(settings.seller_sku_workers),
            "seller_page_workers": str(settings.seller_page_workers),
            "seed_sku_workers": str(settings.seed_sku_workers),
            "retry_failed_now": False,
            "retry_deferred_now": False,
            "retry_rejected_now": False,
            "source_type": "top_list",
            "main_type": "hot",
            "page_size": "50",
            "page_from": "1",
            "page_to": "100",
            "sku": "", "name": "", "category1": "", "category2": "", "category3": "",
            "sales_min": "3", "sales_max": "200", "sold_sum_min": "", "sold_sum_max": "",
            "day_sales_min": "", "day_sales_max": "",
            "avg_price_min": "500", "avg_price_max": "10000",
            "conv_to_cart_pdp_min": "", "conv_to_cart_pdp_max": "",
            "conv_to_cart_search_min": "", "conv_to_cart_search_max": "",
            "sales_dynamics_min": "", "sales_dynamics_max": "",
            "sales_schema": "FBS", "weight_min": "", "weight_max": "5000",
            "avg_delivery_days_min": "", "avg_delivery_days_max": "",
            "create_date_from": "", "create_date_to": "",
            "sort_by": "", "sort_order": "",
            "category_level": "1", "pages_per_category": "5",
        }
        for k, v in defaults.items():
            if k in self._param_vars:
                self._param_vars[k].set(v)
        self._mode_var.set("expand-seller-backlog")
        self._update_linkages()
        
        if GUI_CONFIG_FILE.exists():
            try:
                GUI_CONFIG_FILE.unlink()
            except Exception:
                pass

    def _poll_status(self):
        def check():
            info = {"cdp": "未连接", "db": "未连接", "plugin": "未知", "seller_count": 0}
            try:
                client = BrowserOzonClient()
                cdp = client.ping_cdp()
                if cdp.get("reachable"):
                    pages = cdp.get("pages", [])
                    info["cdp"] = f"已连接 ({len(pages)} 页)"
                    info["plugin"] = "已检测到" if client.extension_dir.exists() else "未找到"
                else:
                    info["cdp"] = "未连接 CDP"
                    info["plugin"] = "未检测"
            except Exception as exc:
                info["cdp"] = f"错误: {exc}"
                info["plugin"] = "检测失败"

            try:
                from . import db
                info["seller_count"] = db.fetch_one("SELECT COUNT(*) AS cnt FROM seller_shops")["cnt"]
                info["db"] = f"已连接 ({info['seller_count']} 卖家)"
            except Exception as exc:
                info["db"] = f"错误: {exc}"
                info["seller_count"] = 0

            self.root.after(0, lambda: self._set_status(info))
            self.root.after(5000, self._poll_status)

        threading.Thread(target=check, daemon=True).start()

    def _set_status(self, info: dict[str, Any]):
        db_text = info.get("db", "")
        db_color = "#27ae60" if "已连接" in db_text else "#e74c3c"
        try:
            from . import db as _db_module
            profile = _db_module.get_active_profile()
            profile_name = profile.name
        except Exception:
            profile_name = "?"
        self._db_label.config(text=f"DB[{profile_name}]: {db_text}", fg=db_color)

        cdp_text = info.get("cdp", "")
        cdp_color = "#27ae60" if "已连接" in cdp_text else "#e74c3c"
        self._cdp_label.config(text=f"CDP: {cdp_text}", fg=cdp_color)

        plugin_text = info.get("plugin", "")
        plugin_color = "#27ae60" if "已检测到" in plugin_text else "#95a5a6"
        self._plugin_label.config(text=f"插件: {plugin_text}", fg=plugin_color)

        self._seller_label.config(text=f"卖家池: {info.get('seller_count', 0)}", fg="#2c3e50")

    def _on_db_profile_changed(self, event=None):
        key = self._db_profile_var.get()
        from . import db
        ok = db.set_active_profile(key)
        if ok:
            self._db_status_label.config(text="已切换", fg="#27ae60")
            self._append_log(f">>> DB已切换至: {key}\n")
            self._poll_status()
        else:
            self._db_status_label.config(text="失败", fg="#e74c3c")

    def _sync_db_structure(self):
        key = self._db_profile_var.get()
        self._sync_btn.config(state="disabled", text="同步中...")
        self._db_status_label.config(text="同步中...", fg="#f39c12")
        def run():
            try:
                from .config import DB_PROFILES
                from .db_sync import sync_table_structure
                target = DB_PROFILES.get(key)
                if target is None:
                    self.root.after(0, lambda: self._db_status_label.config(text="无效配置", fg="#e74c3c"))
                    return
                result = sync_table_structure(target)
                self.root.after(0, lambda: self._sync_done(result))
            except Exception as e:
                self.root.after(0, lambda: self._db_status_label.config(text=f"失败: {e}", fg="#e74c3c"))
            finally:
                self.root.after(0, lambda: self._sync_btn.config(state="normal", text="同步"))
        threading.Thread(target=run, daemon=True).start()

    def _sync_done(self, result):
        if result["success"]:
            self._db_status_label.config(text=f"OK ({result['files']}文件)", fg="#27ae60")
            self._append_log(f">>> 表结构同步完成: {result['files']}文件, {result['statements']}条SQL\n")
        else:
            self._db_status_label.config(text=f"失败({len(result['errors'])}错)", fg="#e74c3c")
            for e in result["errors"][:3]:
                self._append_log(f">>> 同步错误: {e}\n")

    def _reset_runtime_stats(self):
        self._runtime_stats = {
            "mode": "待机",
            "phase": "未开始",
            "current_seller": "暂无",
            "progress": "暂无",
            "latest_issue": "暂无",
        }
        self._sync_runtime_labels()

    def _sync_runtime_labels(self):
        self._mode_label.config(text=f"模式: {self._runtime_stats.get('mode', '待机')}", fg="#2c3e50")
        phase = self._runtime_stats.get("phase", "未开始")
        phase_color = "#e74c3c" if "异常" in phase or "失败" in phase else "#27ae60" if "运行" in phase else "#2c3e50"
        self._phase_label.config(text=f"阶段: {phase}", fg=phase_color)
        self._progress_label.config(text=f"进度: {self._runtime_stats.get('progress', '暂无')}", fg="#2c3e50")
        issue = self._runtime_stats.get("latest_issue", "暂无")
        issue_color = "#e74c3c" if issue != "暂无" else "#2c3e50"
        self._issue_label.config(text=f"异常: {issue}", fg=issue_color)
        self._current_seller_label.config(text=f"当前卖家: {self._runtime_stats.get('current_seller', '暂无')}")

    @staticmethod
    def _extract_metric(text: str, key: str) -> str:
        pos = text.find(key)
        if pos < 0:
            return ""
        pos += len(key)
        chars: list[str] = []
        for ch in text[pos:]:
            if not chars and ch == " ":
                continue
            if ch in {"|", "\n"}:
                break
            chars.append(ch)
        return "".join(chars).strip()

    def _consume_runtime_line(self, line: str):
        stripped = line.strip()
        if not stripped:
            return
        if stripped.startswith("===== 开始采集"):
            self._runtime_stats["phase"] = "运行中"
        elif stripped.startswith("===== 正在停止采集"):
            self._runtime_stats["phase"] = "停止中"
        elif stripped.startswith("===== 采集完成"):
            self._runtime_stats["phase"] = "已完成"
        elif stripped.startswith("expand-network round"):
            fetched = self._extract_metric(stripped, "fetched=")
            self._runtime_stats["phase"] = "卖家队列处理中"
            self._runtime_stats["progress"] = f"本轮待处理卖家={fetched or '?'}"
        elif stripped.startswith("crawl seller:"):
            seller = stripped.split("|", 1)[0].replace("crawl seller:", "").strip()
            items = self._extract_metric(stripped, "items=")
            crawl = self._extract_metric(stripped, "crawl=")
            self._runtime_stats["phase"] = "抓取卖家主页"
            self._runtime_stats["current_seller"] = seller or "暂无"
            self._runtime_stats["progress"] = f"卖家SKU={items or '?'} 抓取耗时={crawl or '?'}"
        elif stripped.startswith("seller home skus prepared:"):
            saved = self._extract_metric(stripped, "saved=")
            mode = self._extract_metric(stripped, "mode=")
            elapsed = self._extract_metric(stripped, "elapsed=")
            self._runtime_stats["phase"] = "批量写入卖家SKU"
            self._runtime_stats["progress"] = f"已准备SKU={saved or '?'} 模式={mode or '?'} 耗时={elapsed or '?'}"
        elif stripped.startswith("prefetch SKU3 batch:"):
            requested = self._extract_metric(stripped, "requested=")
            fetched = self._extract_metric(stripped, "fetched=")
            elapsed = self._extract_metric(stripped, "elapsed=")
            self._runtime_stats["phase"] = "批量预取SKU3"
            self._runtime_stats["progress"] = f"requested={requested or '?'} fetched={fetched or '?'} elapsed={elapsed or '?'}"
        elif stripped.startswith("  progress:"):
            done = self._extract_metric(stripped, "done=")
            qualified = self._extract_metric(stripped, "q=")
            self._runtime_stats["phase"] = "SKU规则判定中"
            self._runtime_stats["progress"] = f"已处理={done or '?'} 达标={qualified or '0'}"
        elif stripped.startswith("seller summary:"):
            skus = self._extract_metric(stripped, "skus=")
            qualified = self._extract_metric(stripped, "qualified=")
            rejected = self._extract_metric(stripped, "rejected=")
            total = self._extract_metric(stripped, "total=")
            self._runtime_stats["phase"] = "单卖家完成"
            self._runtime_stats["progress"] = f"skus={skus or '?'} qualified={qualified or '0'} rejected={rejected or '0'} total={total or '?'}"
        elif any(flag in stripped.lower() for flag in ("traceback", "error:", "write-error", "failed", "manual action required")):
            self._runtime_stats["latest_issue"] = stripped[:120]
            if "phase" not in self._runtime_stats or self._runtime_stats["phase"] == "未开始":
                self._runtime_stats["phase"] = "异常"
        elif stripped.startswith("skip seller"):
            self._runtime_stats["latest_issue"] = stripped[:120]
        self._sync_runtime_labels()

    def _log_tag_for_line(self, line: str) -> str | None:
        lowered = line.lower()
        if any(flag in lowered for flag in ("traceback", "error:", "write-error", "failed", "manual action required")):
            return "error"
        if "warning" in lowered or "skip seller" in lowered:
            return "warning"
        if "summary:" in lowered or "定时状态报告" in line:
            return "summary"
        if "qualified sku:" in lowered or "采集完成" in line:
            return "success"
        if "progress:" in lowered or "prepared:" in lowered or "prefetch sku3 batch:" in lowered:
            return "progress"
        return None

    def _start_log_poller(self):
        while True:
            try:
                text = self._log_queue.get_nowait()
                self._append_log(text)
            except queue.Empty:
                break
        self.root.after(200, self._start_log_poller)

    _MAX_LOG_LINES = 5000

    def _append_log(self, text: str):
        try:
            self._log_area.configure(state="normal")
            segments = text.splitlines(keepends=True)
            if not segments:
                segments = [text]
            for segment in segments:
                if segment.strip():
                    self._consume_runtime_line(segment)
                tag = self._log_tag_for_line(segment)
                if tag:
                    self._log_area.insert("end", segment, tag)
                else:
                    self._log_area.insert("end", segment)
            # 超过上限时截断前面的日志
            line_count = int(self._log_area.index("end-1c").split(".")[0])
            if line_count > self._MAX_LOG_LINES:
                self._log_area.delete("1.0", f"{line_count - self._MAX_LOG_LINES}.0")
            self._log_area.see("end")
            self._log_area.configure(state="disabled")
        except Exception:
            pass

    def _clear_log(self):
        try:
            self._log_area.configure(state="normal")
            self._log_area.delete("1.0", "end")
            self._log_area.configure(state="disabled")
            self._reset_runtime_stats()
        except Exception:
            pass

    def _launch_browser(self):
        self._append_log("===== 正在启动 Chrome 浏览器 =====\n")
        def run():
            try:
                from .cli import build_browser_client
                args = Namespace(
                    profile_dir=str(settings.chrome_profile_dir),
                    extension_dir=str(settings.chrome_extension_dir),
                    chrome_exe=settings.chrome_executable_path or None,
                    channel=settings.chrome_channel,
                    proxy_server=settings.chrome_proxy_server or None,
                    cdp_url=settings.chrome_cdp_url or None,
                    remote_debugging_port=settings.chrome_remote_debugging_port,
                    headless=False
                )
                client = build_browser_client(args)
                client.launch_real_chrome()
            except Exception as exc:
                self._append_log(f"启动浏览器失败: {exc}\n")
        threading.Thread(target=run, daemon=True).start()

    def _restart_browser(self):
        import subprocess, time as _time
        port = str(settings.chrome_remote_debugging_port)
        self._append_log(f"===== 正在重启 Chrome (端口 {port}) =====\n")
        def run():
            try:
                # 1. 杀掉监听端口的 Chrome 进程
                killed = 0
                try:
                    result = subprocess.run(
                        f'netstat -ano | findstr ":{port}" | findstr "LISTENING"',
                        shell=True, capture_output=True, text=True, timeout=5
                    )
                    for line in result.stdout.strip().split("\n"):
                        parts = line.split()
                        if len(parts) >= 5:
                            pid = parts[-1]
                            try:
                                subprocess.run(f'taskkill /F /PID {pid}', shell=True, capture_output=True, timeout=5)
                                killed += 1
                            except Exception:
                                pass
                except Exception:
                    pass
                self._append_log(f"已终止 {killed} 个旧 Chrome 进程\n")
                if killed > 0:
                    _time.sleep(2)

                # 2. 重启 Chrome
                self._append_log("正在启动 Chrome (带毛子插件)...\n")
                from .cli import build_browser_client
                args = Namespace(
                    profile_dir=str(settings.chrome_profile_dir),
                    extension_dir=str(settings.chrome_extension_dir),
                    chrome_exe=settings.chrome_executable_path or None,
                    channel=settings.chrome_channel,
                    proxy_server=settings.chrome_proxy_server or None,
                    cdp_url=None,
                    remote_debugging_port=int(port),
                    headless=False
                )
                client = build_browser_client(args)
                client.launch_real_chrome()

                # 3. 等待 Chrome 就绪
                for _ in range(15):
                    _time.sleep(1)
                    try:
                        import requests
                        r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
                        if r.status_code == 200:
                            self._append_log(f"Chrome 已就绪 (端口 {port})\n")
                            # 检查扩展
                            r2 = requests.get(f"http://127.0.0.1:{port}/json", timeout=2)
                            pages = r2.json()
                            ext = [p for p in pages if "chrome-extension://" in p.get("url", "")]
                            if ext:
                                self._append_log(f"毛子插件已加载 OK\n")
                            else:
                                self._append_log("提示: 未检测到插件页面, 请确认插件已加载\n")
                            return
                    except Exception:
                        pass
                self._append_log("警告: Chrome 启动后未能确认就绪, 请手动检查\n")
            except Exception as exc:
                self._append_log(f"重启浏览器失败: {exc}\n")
        threading.Thread(target=run, daemon=True).start()

    def _start_collection(self):
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("提示", "采集已在运行中")
            return

        mode = self._mode_var.get()

        try:
            params = self._validate_params()
        except ValueError as e:
            messagebox.showerror("参数校验失败", str(e))
            return
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        # Pre-flight: verify CDP browser is reachable before starting collection.
        # Without a logged-in browser, all Ozon/Maozi API calls will fail.
        from .browser_ozon import BrowserOzonClient
        cdp_url = settings.chrome_cdp_url or "http://127.0.0.1:9222"
        preflight = BrowserOzonClient(cdp_url=cdp_url)
        try:
            ping = preflight.ping_cdp()
        except Exception:
            ping = {"reachable": False}
        if not ping.get("reachable"):
            msg = (
                "未检测到 Chrome 浏览器（CDP 调试端口）。\n\n"
                f"请先点击「启动浏览器」按钮，等待 Chrome 窗口出现，\n"
                f"确认已在浏览器中登录 Ozon 和 Maozi 插件后，再点击「开始采集」。\n\n"
                f"（检测地址: {cdp_url}）"
            )
            messagebox.showwarning("浏览器未就绪", msg)
            return

        self._stop_flag.clear()
        self._start_btn.config(state="disabled", bg="#7f8c8d")
        self._stop_btn.config(state="normal", bg="#e74c3c")
        self._start_browser_btn.config(state="disabled", bg="#7f8c8d")
        self._restart_browser_btn.config(state="disabled", bg="#7f8c8d")
        self._clear_log()
        self._logger = _GuiSummaryLogger(self._log_queue)
        self._runtime_stats["mode"] = mode
        self._runtime_stats["phase"] = "启动中"
        self._runtime_stats["current_seller"] = "等待首个卖家..."
        self._runtime_stats["progress"] = "等待日志输出"
        self._runtime_stats["latest_issue"] = "暂无"
        self._sync_runtime_labels()
        self._append_log(f"===== 开始采集 | 模式: {mode} =====\n")
        self._append_log(f"CDP 浏览器已连接: {cdp_url}\n")
        self._append_log("全量实时日志已启用，顶部状态卡会随日志实时刷新，并每30-60秒输出一次运行汇总。\n")

        sys.stdout = self._logger
        sys.stderr = self._logger

        self._worker_thread = threading.Thread(
            target=self._run_collection,
            args=(mode, params),
            daemon=True,
        )
        self._worker_thread.start()

    def _stop_collection(self):
        self._stop_flag.set()
        self._append_log("\n===== 正在停止采集，将在当前卖家结束后退出... =====\n")
        self._stop_btn.config(state="disabled", bg="#7f8c8d")

    def _run_collection(self, mode: str, user_params: dict[str, Any]):
        try:
            from .cli import (
                cmd_expand_seller_backlog,
                cmd_crawl_top_list_network,
                cmd_expand_seed_pool_network,
                cmd_multi_category_network,
                set_verbose,
            )

            set_verbose(False)
            
            # Combine user params with fixed environment params
            full_params = {
                "profile_dir": str(settings.chrome_profile_dir),
                "extension_dir": str(settings.chrome_extension_dir),
                "chrome_exe": settings.chrome_executable_path or None,
                "channel": settings.chrome_channel,
                "proxy_server": settings.chrome_proxy_server or None,
                "cdp_url": settings.chrome_cdp_url or None,
                "remote_debugging_port": settings.chrome_remote_debugging_port,
                "headless": settings.chrome_headless,
                "stop_event": self._stop_flag,
            }
            full_params.update(user_params)
            
            # Handle env vars
            if "batch_size" in full_params:
                os.environ["TOP_LIST_SKU3_BATCH_SIZE"] = str(full_params["batch_size"])
            if "concurrency" in full_params:
                os.environ["TOP_LIST_SKU3_BATCH_CONCURRENCY"] = str(full_params["concurrency"])
            if "chunk_delay_ms" in full_params:
                os.environ["TOP_LIST_SKU3_BATCH_CHUNK_DELAY_MS"] = str(full_params["chunk_delay_ms"])

            args = Namespace(**full_params)

            if mode == "expand-seller-backlog":
                cmd_expand_seller_backlog(args)
            elif mode == "crawl-top-list-network":
                # Supply default empty fields for unconfigured top list params
                for f in ["sku", "name", "category1", "category2", "category3", "sales_min", "sales_max",
                         "day_sales_min", "day_sales_max", "avg_price_min", "avg_price_max",
                         "sales_dynamics_min", "sales_dynamics_max", "conv_to_cart_pdp_min",
                         "conv_to_cart_pdp_max", "conv_to_cart_search_min", "conv_to_cart_search_max",
                         "sales_schema", "sold_sum_min", "sold_sum_max",
                         "weight_min", "weight_max",
                         "avg_delivery_days_min", "avg_delivery_days_max",
                         "create_date_from", "create_date_to", "sort_by", "sort_order"]:
                    if not hasattr(args, f):
                        setattr(args, f, "")
                setattr(args, "refresh_hours", 24)
                setattr(args, "force_refresh", False)
                setattr(args, "skip_process", False)
                setattr(args, "retry_failed_now", False)
                setattr(args, "retry_deferred_now", False)
                setattr(args, "retry_rejected_now", False)
                cmd_crawl_top_list_network(args)
            elif mode == "expand-seed-pool-network":
                if not hasattr(args, "query_key"):
                    setattr(args, "query_key", "")
                cmd_expand_seed_pool_network(args)
            elif mode == "multi-category-network":
                # 多类目模式复用榜单参数
                for f in ["sku", "name", "category1", "category2", "category3", "sales_min", "sales_max",
                         "day_sales_min", "day_sales_max", "avg_price_min", "avg_price_max",
                         "sales_dynamics_min", "sales_dynamics_max", "conv_to_cart_pdp_min",
                         "conv_to_cart_pdp_max", "conv_to_cart_search_min", "conv_to_cart_search_max",
                         "sales_schema", "sold_sum_min", "sold_sum_max",
                         "weight_min", "weight_max",
                         "avg_delivery_days_min", "avg_delivery_days_max",
                         "create_date_from", "create_date_to", "sort_by", "sort_order"]:
                    if not hasattr(args, f):
                        setattr(args, f, "")
                setattr(args, "refresh_hours", 24)
                setattr(args, "force_refresh", False)
                setattr(args, "skip_process", False)
                setattr(args, "retry_failed_now", False)
                setattr(args, "retry_deferred_now", False)
                setattr(args, "retry_rejected_now", False)
                if not hasattr(args, "category_level"):
                    setattr(args, "category_level", "1")
                if not hasattr(args, "pages_per_category"):
                    setattr(args, "pages_per_category", "5")
                cmd_multi_category_network(args)
            else:
                print(f"未知采集模式: {mode}")
        except SystemExit:
            pass
        except Exception as exc:
            traceback.print_exc()
            notify_collection_failed(
                mode,
                detail=f"GUI采集线程异常退出",
                exc=exc,
            )
        finally:
            sys.stdout = self._old_stdout
            sys.stderr = self._old_stderr
            self.root.after(0, self._collection_finished)

    def _collection_finished(self):
        self._append_log("\n===== 采集完成 =====\n")
        self._runtime_stats["phase"] = "已完成"
        self._sync_runtime_labels()
        self._start_btn.config(state="normal", bg="#27ae60")
        self._stop_btn.config(state="disabled", bg="#7f8c8d")
        self._start_browser_btn.config(state="normal", bg="#3498db")
        self._restart_browser_btn.config(state="normal", bg="#e67e22")
        self._stop_flag.clear()
        self._worker_thread = None

    def _on_close(self):
        if self._worker_thread and self._worker_thread.is_alive():
            if not messagebox.askyesno("确认退出", "采集正在运行中，确定退出吗？"):
                return
            self._stop_collection()
        self.root.destroy()


def main():
    root = tk.Tk()
    _app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
