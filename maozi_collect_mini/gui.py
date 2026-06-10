"""GUI 图形界面。

功能：
- 采集模式选择：榜单采集 / 卖家主页采集
- 参数配置（类目层级、页数、过滤条件等）
- 暂存按钮：将GUI配置写入 config.json
- 启动/停止采集
- 实时日志显示
- CDP连接状态检测
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from datetime import datetime, timedelta
from tkinter import Frame, Label, Button, Entry, ttk, messagebox, scrolledtext, BooleanVar, StringVar
from typing import Any

from .config import ROOT_DIR, CONFIG_FILE, settings, load_runtime_config, save_runtime_config
from .browser import BrowserClient

class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 25
        y = self.widget.winfo_rooty() + 25
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(1)
        self.tip_window.wm_geometry(f"+{x}+{y}")
        label = Label(
            self.tip_window, text=self.text, justify="left",
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("Microsoft YaHei", 8),
        )
        label.pack()

    def hide(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("毛子采集 Mini 控制台")
        self.root.geometry("1050x700")
        self.root.minsize(900, 560)
        self.root.configure(bg="#f5f6fa")

        self._stop_flag = threading.Event()
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._param_vars: dict[str, tk.Variable] = {}
        self._param_widgets: dict[str, Any] = {}
        self._mode_var = tk.StringVar(value="ranking")
        self._running = False

        self._build_ui()
        self._load_config()
        self._update_linkages()
        self._poll_status()
        self._start_log_poller()

    # ============================================================
    # UI 构建
    # ============================================================
    def _build_ui(self):
        # 顶部状态栏
        info_bar = Label(
            self.root, text="毛子采集 Mini — Ozon 选品流水线",
            bg="#2c3e50", fg="white", font=("Microsoft YaHei", 10, "bold"), pady=4,
        )
        info_bar.pack(fill="x")

        # 运行状态面板
        dashboard = Frame(self.root, bg="#ffffff", padx=8, pady=4, relief="ridge", bd=1)
        dashboard.pack(fill="x", padx=4, pady=(4, 2))

        self._cdp_label = Label(dashboard, text="CDP: 检测中...", bg="#ffffff", font=("Microsoft YaHei", 8))
        self._cdp_label.grid(row=0, column=0, padx=(0, 12), sticky="w")
        self._db_label = Label(dashboard, text="DB: 检测中...", bg="#ffffff", font=("Microsoft YaHei", 8))
        self._db_label.grid(row=0, column=1, padx=(0, 12), sticky="w")
        self._status_label = Label(dashboard, text="状态: 待机", bg="#ffffff", font=("Microsoft YaHei", 8))
        self._status_label.grid(row=0, column=2, sticky="w")

        # 主体区域
        main_frame = Frame(self.root, bg="#f5f6fa")
        main_frame.pack(fill="both", expand=True, padx=4, pady=2)

        # 左侧控制面板
        control_panel = Frame(main_frame, bg="#ffffff", padx=8, pady=6, relief="ridge", bd=1)
        control_panel.pack(side="left", fill="y", expand=False, padx=(2, 1))

        # 模式选择
        Label(control_panel, text="采集模式", bg="#ffffff", font=("Microsoft YaHei", 9, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        mode_frame = Frame(control_panel, bg="#ffffff")
        mode_frame.grid(row=1, column=0, sticky="w", pady=(0, 6))
        modes = [
            ("榜单采集", "ranking"),
            ("卖家主页采集", "seller"),
            ("类目页采集", "category_page"),
        ]
        for idx, (text, value) in enumerate(modes):
            ttk.Radiobutton(
                mode_frame, text=text, variable=self._mode_var,
                value=value, command=self._update_linkages,
            ).grid(row=0, column=idx, padx=(0, 10))

        # 参数配置 Notebook
        self._notebook = ttk.Notebook(control_panel)
        self._notebook.grid(row=2, column=0, sticky="ew", pady=(4, 2))

        tab_ranking = Frame(self._notebook, bg="#ffffff", padx=6, pady=6)
        tab_seller = Frame(self._notebook, bg="#ffffff", padx=6, pady=6)
        tab_category = Frame(self._notebook, bg="#ffffff", padx=6, pady=6)
        tab_loop = Frame(self._notebook, bg="#ffffff", padx=6, pady=6)

        self._notebook.add(tab_ranking, text="榜单配置")
        self._notebook.add(tab_seller, text="卖家配置")
        self._notebook.add(tab_category, text="类目页配置")
        self._notebook.add(tab_loop, text="循环控制")

        # Tab 1: 榜单配置
        self._add_param(tab_ranking, 0, 0, "榜单类型", "main_type", "hot", "str",
                       tooltip="hot=热销, new=新品, potential=潜力")
        self._add_param(tab_ranking, 0, 1, "类目层级", "category_level", "1", "str",
                       tooltip="0=不选类目(全榜), 1=遍历一级类目, 2=遍历二级, 3=遍历三级")
        self._add_param(tab_ranking, 1, 0, "起始页码", "page_from", "1", "int", min_val=1, max_val=1000,
                       tooltip="从第几页开始采集")
        self._add_param(tab_ranking, 1, 1, "结束页码", "page_to", "100", "int", min_val=1, max_val=1000,
                       tooltip="采集到第几页停止")

        self._add_param(tab_ranking, 2, 0, "月销量≥", "sales_min", "3", "str",
                       tooltip="月销量最小值(榜单API过滤)")
        self._add_param(tab_ranking, 2, 1, "月销量≤", "sales_max", "200", "str",
                       tooltip="月销量最大值")
        self._add_param(tab_ranking, 3, 0, "均价≥(₽)", "avg_price_min", "500", "str",
                       tooltip="均价最小值(卢布)")
        self._add_param(tab_ranking, 3, 1, "均价≤(₽)", "avg_price_max", "10000", "str",
                       tooltip="均价最大值(卢布)")
        self._add_param(tab_ranking, 4, 0, "发货模式", "sales_schema", "FBS", "str",
                       tooltip="发货模式: FBS, FBP, rFBS")
        self._add_param(tab_ranking, 4, 1, "重量≤(g)", "weight_max", "5000", "str",
                       tooltip="商品重量最大值(克)")
        gui_create_from = (datetime.now().date() - timedelta(days=201)).strftime("%Y-%m-%d")
        gui_create_to = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
        self._add_param(tab_ranking, 5, 0, "上架起始", "create_date_from", gui_create_from, "str",
                       tooltip="上架起始日期 YYYY-MM-DD")
        self._add_param(tab_ranking, 5, 1, "上架截止", "create_date_to", gui_create_to, "str",
                       tooltip="上架截止日期 YYYY-MM-DD")
        self._add_param(tab_ranking, 6, 0, "断点续采", "resume_from_checkpoint", False, "bool",
                       tooltip="勾选后跳过已完成类目，从上次中断位置继续采集。取消勾选则全新开始")

        # Tab 2: 卖家配置
        self._add_param(tab_seller, 0, 0, "每轮处理上限", "seller_limit", "0", "int", min_val=0, max_val=10000,
                       tooltip="每轮最多处理卖家数, 0=不限制")
        self._add_param(tab_seller, 0, 1, "卖家页超时(秒)", "seller_page_timeout", str(settings.seller_page_timeout_seconds), "int", min_val=10, max_val=3600,
                       tooltip="单个卖家页面加载超时时间")

        # Tab 3: 类目页配置
        self._add_param(tab_category, 0, 0, "叶子层级", "leaf_levels", "4", "str",
                       tooltip="遍历的叶子层级，4=四级类目(优先), 4,3=先四级后三级, 3=仅三级")
        self._add_param(tab_category, 0, 1, "排序方式", "sorting", "score", "str",
                       tooltip="score=流行度, new=新品, price=价格从低到高")
        self._add_param(tab_category, 1, 0, "价格分段(RUB)", "price_ranges", "220.000;1100.000,1100.000;3300.000,3300.000;11000.000", "str",
                       tooltip="逗号分隔的价格分段，每段格式 from.000;to.000。留空使用默认三段(约20-1000CNY)")
        self._add_param(tab_category, 1, 1, "类目翻页上限", "max_pages", "0", "int", min_val=0, max_val=5000,
                       tooltip="每个类目最多翻多少页，0=不限制")
        self._add_param(tab_category, 2, 0, "断点续采", "resume_from_checkpoint", False, "bool",
                       tooltip="勾选后跳过已完成类目+价格分段，从上次中断位置继续。取消勾选则全新开始")

        # Tab 4: 循环控制
        self._add_param(tab_loop, 0, 0, "无人值守循环", "forever", True, "bool",
                       tooltip="勾选后采集完成自动循环, 不退出")
        self._add_param(tab_loop, 0, 1, "空闲等待(秒)", "idle_sleep_seconds", str(settings.collection_idle_sleep_seconds), "int", min_val=1, max_val=86400,
                       tooltip="循环模式下每轮完成后的等待时间")

        # 按钮区
        btn_frame = Frame(control_panel, bg="#ffffff")
        btn_frame.grid(row=3, column=0, sticky="w", pady=(12, 0))

        self._start_browser_btn = Button(
            btn_frame, text="启动浏览器", bg="#3498db", fg="white",
            font=("Microsoft YaHei", 9, "bold"), width=10, pady=3,
            command=self._launch_browser, relief="flat",
        )
        self._start_browser_btn.pack(side="left", padx=(0, 6))

        self._start_btn = Button(
            btn_frame, text="▶ 开始采集", bg="#27ae60", fg="white",
            font=("Microsoft YaHei", 9, "bold"), width=10, pady=3,
            command=self._start_collection, relief="flat",
        )
        self._start_btn.pack(side="left", padx=(0, 6))

        self._stop_btn = Button(
            btn_frame, text="■ 停止", bg="#e74c3c", fg="white",
            font=("Microsoft YaHei", 9, "bold"), width=8, pady=3,
            command=self._stop_collection, relief="flat", state="disabled",
        )
        self._stop_btn.pack(side="left", padx=(0, 6))

        self._save_btn = Button(
            btn_frame, text="暂存配置", bg="#f39c12", fg="white",
            font=("Microsoft YaHei", 9, "bold"), width=10, pady=3,
            command=self._save_config, relief="flat",
        )
        self._save_btn.pack(side="left", padx=(0, 6))

        self._clear_btn = Button(
            btn_frame, text="清空日志", bg="#95a5a6", fg="white",
            font=("Microsoft YaHei", 8), width=8, pady=3,
            command=self._clear_log, relief="flat",
        )
        self._clear_btn.pack(side="left")

        self._reset_checkpoint_btn = Button(
            btn_frame, text="清除断点", bg="#e67e22", fg="white",
            font=("Microsoft YaHei", 8), width=8, pady=3,
            command=self._clear_checkpoints, relief="flat",
        )
        self._reset_checkpoint_btn.pack(side="left", padx=(6, 0))

        self._clear_category_cp_btn = Button(
            btn_frame, text="清除类目断点", bg="#e67e22", fg="white",
            font=("Microsoft YaHei", 8), width=10, pady=3,
            command=self._clear_category_checkpoints, relief="flat",
        )
        self._clear_category_cp_btn.pack(side="left", padx=(6, 0))

        # 右侧日志区
        log_frame = Frame(main_frame, bg="#ffffff", padx=6, pady=4, relief="ridge", bd=1)
        log_frame.pack(side="right", fill="both", expand=True, padx=(1, 2))
        Label(log_frame, text="运行日志", bg="#ffffff", font=("Microsoft YaHei", 9, "bold")).pack(anchor="w", pady=(0, 2))

        self._log_area = scrolledtext.ScrolledText(
            log_frame, wrap="word", font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white", state="normal",
        )
        self._log_area.pack(fill="both", expand=True)
        self._log_area.configure(state="disabled")
        self._log_area.tag_configure("error", foreground="#ff6b6b")
        self._log_area.tag_configure("warning", foreground="#ffd166")
        self._log_area.tag_configure("success", foreground="#72efdd")

    # ============================================================
    # 参数控件
    # ============================================================
    def _add_param(self, parent, row, col, label, key, default, ptype, min_val=None, max_val=None, tooltip=""):
        frame = Frame(parent, bg="#ffffff")
        frame.grid(row=row, column=col, sticky="w", padx=6, pady=2)

        lbl_text = label
        if tooltip:
            lbl_text += " (?)"
        lbl = Label(frame, text=lbl_text, bg="#ffffff", font=("Microsoft YaHei", 8), cursor="question_arrow")
        lbl.pack(side="left", padx=(0, 3))
        if tooltip:
            ToolTip(lbl, tooltip)

        if ptype == "bool":
            var = BooleanVar(value=bool(default))
            ent = ttk.Checkbutton(frame, variable=var)
            ent.pack(side="left")
        else:
            var = StringVar(value=str(default))
            ent = Entry(frame, textvariable=var, width=10, font=("Consolas", 8), relief="sunken", bd=1)
            ent.pack(side="left")
            ent.validation_rules = {"ptype": ptype, "min": min_val, "max": max_val, "label": label}

        self._param_widgets[key] = ent
        self._param_vars[key] = var

    def _update_linkages(self):
        mode = self._mode_var.get()
        is_ranking = (mode == "ranking")
        is_category = (mode == "category_page")
        ranking_keys = [
            "main_type", "category_level", "page_from", "page_to",
            "sales_min", "sales_max", "avg_price_min", "avg_price_max",
            "sales_schema", "weight_max", "create_date_from", "create_date_to",
        ]
        category_keys = [
            "leaf_levels", "sorting", "price_ranges", "max_pages",
        ]
        for key in ranking_keys:
            if key in self._param_widgets:
                state = "normal" if is_ranking else "disabled"
                self._param_widgets[key].configure(state=state)
        for key in category_keys:
            if key in self._param_widgets:
                state = "normal" if is_category else "disabled"
                self._param_widgets[key].configure(state=state)

    # ============================================================
    # 配置存取
    # ============================================================
    def _save_config(self):
        try:
            config = {k: v.get() for k, v in self._param_vars.items()}
            config["_mode"] = self._mode_var.get()
            save_runtime_config(config)
            self._append_log(">>> 配置已暂存到 config.json\n")
            messagebox.showinfo("成功", "配置已暂存到 config.json")
        except Exception as e:
            messagebox.showerror("失败", str(e))

    # 日期字段永不做持久化覆盖（始终采用动态默认值）
    _EPHEMERAL_KEYS = {"create_date_from", "create_date_to"}

    def _load_config(self):
        try:
            config = load_runtime_config()
            if "_mode" in config:
                self._mode_var.set(config.pop("_mode"))
            for k, v in config.items():
                if k in self._param_vars and k not in self._EPHEMERAL_KEYS:
                    self._param_vars[k].set(v)
        except Exception:
            pass

    # ============================================================
    # 采集控制
    # ============================================================
    def _start_collection(self):
        if self._running:
            messagebox.showwarning("提示", "采集已在运行中")
            return
        self._save_config()
        self._running = True
        self._stop_flag.clear()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._status_label.config(text="状态: 运行中", fg="#27ae60")

        def run():
            try:
                from .main import run_pipeline
                old_stdout = sys.stdout
                sys.stdout = _LogRedirector(self._log_queue)
                try:
                    run_pipeline(stop_flag=self._stop_flag)
                finally:
                    sys.stdout = old_stdout
            except Exception as exc:
                self._log_queue.put(f"[ERROR] {traceback.format_exc()}\n")
            finally:
                self.root.after(0, self._collection_done)

        threading.Thread(target=run, daemon=True).start()

    def _stop_collection(self):
        self._stop_flag.set()
        self._stop_btn.configure(state="disabled")
        self._status_label.config(text="状态: 停止中...", fg="#e74c3c")
        self._append_log("===== 正在停止采集... =====\n")

    def _collection_done(self):
        self._running = False
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._status_label.config(text="状态: 待机", fg="#2c3e50")
        self._append_log("===== 采集完成 =====\n")

    def _launch_browser(self):
        self._append_log("===== 正在启动 Chrome 浏览器 =====\n")
        def run():
            try:
                client = BrowserClient(headless=False)
                client.launch_real_chrome()
                self._append_log(">>> Chrome 已启动\n")
            except Exception as exc:
                self._append_log(f">>> 启动失败: {exc}\n")
        threading.Thread(target=run, daemon=True).start()

    # ============================================================
    # 状态轮询
    # ============================================================
    def _poll_status(self):
        def check():
            cdp_text = "未连接"
            db_text = "未连接"
            try:
                client = BrowserClient()
                cdp = client.ping_cdp()
                cdp_text = "已连接" if cdp.get("reachable") else "未连接"
            except Exception:
                cdp_text = "检测失败"
            try:
                from .repository import count_sku_products, count_seller_shops
                products = count_sku_products()
                sellers = count_seller_shops()
                db_text = f"已连接 (SKU:{products}, 卖家:{sellers})"
            except Exception:
                db_text = "检测失败"
            self.root.after(0, lambda: self._set_status(cdp_text, db_text))
            self.root.after(5000, self._poll_status)
        threading.Thread(target=check, daemon=True).start()

    def _set_status(self, cdp_text, db_text):
        cdp_color = "#27ae60" if "已连接" in cdp_text else "#e74c3c"
        db_color = "#27ae60" if "已连接" in db_text else "#e74c3c"
        self._cdp_label.config(text=f"CDP: {cdp_text}", fg=cdp_color)
        self._db_label.config(text=f"DB: {db_text}", fg=db_color)

    # ============================================================
    # 日志
    # ============================================================
    def _start_log_poller(self):
        while True:
            try:
                text = self._log_queue.get_nowait()
                self._append_log(text)
            except queue.Empty:
                break
        self.root.after(200, self._start_log_poller)

    def _append_log(self, text: str):
        try:
            self._log_area.configure(state="normal")
            lowered = text.lower()
            tag = None
            if any(w in lowered for w in ("error", "traceback", "failed")):
                tag = "error"
            elif any(w in lowered for w in ("warn", "skip")):
                tag = "warning"
            elif any(w in lowered for w in ("完成", "qualified", "ok", "success")):
                tag = "success"

            if tag:
                self._log_area.insert("end", text, tag)
            else:
                self._log_area.insert("end", text)
            self._log_area.see("end")
            self._log_area.configure(state="disabled")
        except Exception:
            pass

    def _clear_log(self):
        try:
            self._log_area.configure(state="normal")
            self._log_area.delete("1.0", "end")
            self._log_area.configure(state="disabled")
        except Exception:
            pass

    def _clear_checkpoints(self):
        """清除所有断点记录"""
        if not messagebox.askyesno("确认", "确定要清除所有断点记录吗？\n下次采集将从头开始。"):
            return
        try:
            from .repository import clear_all_checkpoints
            count = clear_all_checkpoints()
            self._append_log(f">>> 已清除 {count} 条断点记录\n")
            messagebox.showinfo("成功", f"已清除 {count} 条断点记录")
        except Exception as e:
            messagebox.showerror("失败", str(e))

    def _clear_category_checkpoints(self):
        """清除类目页采集断点记录"""
        if not messagebox.askyesno("确认", "确定要清除类目页采集断点记录吗？\n下次采集将从头开始。"):
            return
        try:
            from .repository import clear_category_page_checkpoints
            count = clear_category_page_checkpoints()
            self._append_log(f">>> 已清除 {count} 条类目页断点记录\n")
            messagebox.showinfo("成功", f"已清除 {count} 条类目页断点记录")
        except Exception as e:
            messagebox.showerror("失败", str(e))


class _LogRedirector:
    def __init__(self, q: queue.Queue[str]):
        self._q = q
        self._buf = ""

    def write(self, text: str):
        self._buf += text
        if "\n" in self._buf:
            self._q.put(self._buf)
            self._buf = ""

    def flush(self):
        if self._buf:
            self._q.put(self._buf)
            self._buf = ""


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
