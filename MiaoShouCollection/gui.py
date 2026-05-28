"""妙手采集 - GUI 控制界面

功能：
1. 打开 9224 浏览器远程调试端口，预登录妙手
2. 跳转到指定 URL
3. 开始运行 RPA
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import (
    Frame, Label, Button, Entry, ttk, messagebox,
    scrolledtext, StringVar, Toplevel,
)
from typing import Any

import pymysql
from playwright.sync_api import sync_playwright

GUI_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "gui_config.json")

CDP_URL = "http://127.0.0.1:9224"
DEFAULT_TARGET_URL = "https://erp.91miaoshou.com/common_collect_box/index?fetchType=aliCrossSaleSports"

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "root",
    "database": "ozon_selection",
}

IMAGE_SEARCH_API = "mtop.com.alibaba.global.select.aibuy.image.search"


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
        tw.wm_geometry(f"+{x}+{y}")
        label = Label(
            tw, text=self.text, justify="left",
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("Microsoft YaHei", "8", "normal"),
        )
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        tw = self.tip_window
        self.tip_window = None
        if tw:
            tw.destroy()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("妙手采集控制台 (v1.0)")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        self.root.configure(bg="#f5f6fa")

        self._stop_flag = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr

        self._url_var = StringVar(value=DEFAULT_TARGET_URL)
        self._batch_limit_var = StringVar(value="10")

        self._build_ui()
        self._load_config()
        self._poll_status()
        self._start_log_poller()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        try:
            self._build_ui_inner()
        except Exception:
            traceback.print_exc()

    def _build_ui_inner(self):
        # 顶部标题栏
        info_bar = Label(
            self.root,
            text="妙手采集 — 1688 图搜 RPA 控制台",
            bg="#2c3e50",
            fg="white",
            font=("Microsoft YaHei", 11, "bold"),
            pady=6,
        )
        info_bar.pack(fill="x")

        # 状态面板
        dashboard = Frame(self.root, bg="#ffffff", padx=12, pady=8, relief="ridge", bd=1)
        dashboard.pack(fill="x", padx=8, pady=(8, 4))

        Label(dashboard, text="运行状态", bg="#ffffff", font=("Microsoft YaHei", 10, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 4)
        )

        self._cdp_label = Label(dashboard, text="CDP: 检测中...", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._cdp_label.grid(row=1, column=0, padx=(0, 10), sticky="w")

        self._db_label = Label(dashboard, text="DB:  检测中...", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._db_label.grid(row=1, column=1, padx=(0, 10), sticky="w")

        self._pending_label = Label(dashboard, text="待处理: 检测中...", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._pending_label.grid(row=1, column=2, padx=(0, 10), sticky="w")

        self._phase_label = Label(dashboard, text="阶段: 待机", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._phase_label.grid(row=2, column=0, padx=(0, 10), sticky="w")

        self._progress_label = Label(dashboard, text="进度: 暂无", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._progress_label.grid(row=2, column=1, padx=(0, 10), sticky="w")

        self._issue_label = Label(dashboard, text="异常: 暂无", bg="#ffffff", font=("Microsoft YaHei", 9))
        self._issue_label.grid(row=2, column=2, sticky="w")

        # 控制面板
        control_panel = Frame(self.root, bg="#ffffff", padx=12, pady=8, relief="ridge", bd=1)
        control_panel.pack(fill="x", padx=8, pady=4)

        Label(control_panel, text="浏览器控制", bg="#ffffff", font=("Microsoft YaHei", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )

        # 第一行：启动浏览器
        browser_frame = Frame(control_panel, bg="#ffffff")
        browser_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        self._launch_btn = Button(
            browser_frame, text="启动浏览器 (9224)", bg="#3498db", fg="white",
            font=("Microsoft YaHei", 10, "bold"), width=18, padx=8, pady=4,
            command=self._launch_browser, relief="flat",
        )
        self._launch_btn.pack(side="left", padx=(0, 8))

        self._connect_btn = Button(
            browser_frame, text="连接已有浏览器", bg="#2980b9", fg="white",
            font=("Microsoft YaHei", 10), width=16, padx=8, pady=4,
            command=self._connect_browser, relief="flat",
        )
        self._connect_btn.pack(side="left", padx=(0, 8))

        Label(browser_frame, text="端口: 9224", bg="#ffffff", font=("Microsoft YaHei", 9), fg="#7f8c8d").pack(
            side="left"
        )

        # 第二行：URL 跳转
        url_frame = Frame(control_panel, bg="#ffffff")
        url_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        Label(url_frame, text="目标URL:", bg="#ffffff", font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))

        self._url_entry = Entry(url_frame, textvariable=self._url_var, font=("Consolas", 9), width=60)
        self._url_entry.pack(side="left", padx=(0, 8), fill="x", expand=True)

        self._nav_btn = Button(
            url_frame, text="跳转", bg="#8e44ad", fg="white",
            font=("Microsoft YaHei", 9, "bold"), width=6, padx=4, pady=2,
            command=self._navigate_url, relief="flat",
        )
        self._nav_btn.pack(side="left", padx=(0, 4))

        self._default_url_btn = Button(
            url_frame, text="默认URL", bg="#9b59b6", fg="white",
            font=("Microsoft YaHei", 9), width=8, padx=4, pady=2,
            command=self._set_default_url, relief="flat",
        )
        self._default_url_btn.pack(side="left")

        # 第三行：RPA 控制
        rpa_frame = Frame(control_panel, bg="#ffffff")
        rpa_frame.grid(row=3, column=0, sticky="ew", pady=(0, 4))

        Label(rpa_frame, text="RPA 控制", bg="#ffffff", font=("Microsoft YaHei", 10, "bold")).grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 4)
        )

        btn_row = Frame(rpa_frame, bg="#ffffff")
        btn_row.grid(row=1, column=0, sticky="w")

        self._start_btn = Button(
            btn_row, text="开始 RPA", bg="#27ae60", fg="white",
            font=("Microsoft YaHei", 10, "bold"), width=12, padx=8, pady=4,
            command=self._start_rpa, relief="flat",
        )
        self._start_btn.pack(side="left", padx=(0, 8))

        self._stop_btn = Button(
            btn_row, text="停止 RPA", bg="#e74c3c", fg="white",
            font=("Microsoft YaHei", 10, "bold"), width=12, padx=8, pady=4,
            command=self._stop_rpa, relief="flat", state="disabled",
        )
        self._stop_btn.pack(side="left", padx=(0, 8))

        self._batch_btn = Button(
            btn_row, text="批量处理", bg="#f39c12", fg="white",
            font=("Microsoft YaHei", 10, "bold"), width=12, padx=8, pady=4,
            command=self._batch_process, relief="flat",
        )
        self._batch_btn.pack(side="left", padx=(0, 8))

        # 批量处理数量
        Label(btn_row, text="批量数量:", bg="#ffffff", font=("Microsoft YaHei", 9)).pack(side="left", padx=(8, 4))
        Entry(btn_row, textvariable=self._batch_limit_var, width=6, font=("Consolas", 9)).pack(side="left")

        # 配置按钮行
        config_row = Frame(rpa_frame, bg="#ffffff")
        config_row.grid(row=2, column=0, sticky="w", pady=(8, 0))

        self._save_btn = Button(
            config_row, text="保存配置", bg="#f39c12", fg="white",
            font=("Microsoft YaHei", 9), padx=8, pady=2,
            command=self._save_config, relief="flat",
        )
        self._save_btn.pack(side="left", padx=(0, 8))

        self._clear_btn = Button(
            config_row, text="清空日志", bg="#95a5a6", fg="white",
            font=("Microsoft YaHei", 9), padx=8, pady=2,
            command=self._clear_log, relief="flat",
        )
        self._clear_btn.pack(side="left")

        # 筛选条件配置
        filter_frame = Frame(control_panel, bg="#ffffff")
        filter_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))

        Label(filter_frame, text="图搜筛选条件", bg="#ffffff", font=("Microsoft YaHei", 10, "bold")).grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 4)
        )

        self._filter_vars = {}
        filters = ["一件代发", "包邮", "先采后付", "三无包赔", "24H发货", "48H发货", "件重尺已校准", "7天无理由退货"]
        for idx, f in enumerate(filters):
            var = tk.BooleanVar(value=f in ["一件代发", "24H发货", "件重尺已校准"])
            self._filter_vars[f] = var
            ttk.Checkbutton(filter_frame, text=f, variable=var).grid(
                row=1 + idx // 4, column=idx % 4, padx=(0, 12), sticky="w"
            )

        # 日志区
        log_frame = Frame(self.root, bg="#ffffff", padx=12, pady=8, relief="ridge", bd=1)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        Label(log_frame, text="运行日志", bg="#ffffff", font=("Microsoft YaHei", 10, "bold")).pack(
            anchor="w", pady=(0, 4)
        )

        self._log_area = scrolledtext.ScrolledText(
            log_frame, wrap="word", font=("Consolas", 9),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
        )
        self._log_area.pack(fill="both", expand=True)
        self._log_area.configure(state="disabled")
        self._log_area.tag_configure("error", foreground="#ff6b6b")
        self._log_area.tag_configure("warning", foreground="#ffd166")
        self._log_area.tag_configure("success", foreground="#72efdd")
        self._log_area.tag_configure("progress", foreground="#a29bfe")
        self._log_area.tag_configure("info", foreground="#4cc9f0")

    # ========== 浏览器控制 ==========

    def _launch_browser(self):
        """启动 Chrome 并开启 9224 远程调试端口"""
        self._append_log("正在启动 Chrome 浏览器 (端口 9224)...\n", "info")

        def run():
            try:
                chrome_path = self._detect_chrome()
                if not chrome_path:
                    self._append_log("未找到 Chrome 浏览器，请手动指定路径\n", "error")
                    return

                profile_dir = os.path.join(os.path.dirname(__file__), "profiles", "profile-001")
                os.makedirs(profile_dir, exist_ok=True)

                cmd = [
                    chrome_path,
                    f"--remote-debugging-port=9224",
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled",
                    "--no-proxy-server",
                ]

                self._append_log(f"Chrome 路径: {chrome_path}\n")
                self._append_log(f"Profile: {profile_dir}\n")

                subprocess.Popen(cmd)
                self._append_log("Chrome 已启动，请在浏览器中登录妙手 ERP\n", "success")

            except Exception as e:
                self._append_log(f"启动浏览器失败: {e}\n", "error")

        threading.Thread(target=run, daemon=True).start()

    def _connect_browser(self):
        """连接到已有的 9224 端口浏览器"""
        self._append_log("正在连接 9224 端口...\n", "info")

        def run():
            try:
                pw = sync_playwright().start()
                browser = pw.chromium.connect_over_cdp(CDP_URL)
                pages = browser.contexts[0].pages if browser.contexts else []
                self._append_log(f"连接成功！当前 {len(pages)} 个页面\n", "success")
                for i, p in enumerate(pages):
                    self._append_log(f"  [{i}] {p.url}\n")
                browser.close()
                pw.stop()
            except Exception as e:
                self._append_log(f"连接失败: {e}\n", "error")

        threading.Thread(target=run, daemon=True).start()

    def _navigate_url(self):
        """跳转到指定 URL"""
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入目标 URL")
            return

        self._append_log(f"正在跳转: {url}\n", "info")

        def run():
            try:
                pw = sync_playwright().start()
                browser = pw.chromium.connect_over_cdp(CDP_URL)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._append_log(f"跳转成功: {page.title()}\n", "success")
                self._append_log(f"URL: {page.url}\n")
                # 不关闭浏览器，保持页面打开
                pw.stop()
            except Exception as e:
                self._append_log(f"跳转失败: {e}\n", "error")

        threading.Thread(target=run, daemon=True).start()

    def _set_default_url(self):
        self._url_var.set(DEFAULT_TARGET_URL)

    # ========== RPA 控制 ==========

    def _start_rpa(self):
        """开始运行 RPA"""
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("提示", "RPA 正在运行中")
            return

        self._stop_flag.clear()
        self._start_btn.config(state="disabled", bg="#7f8c8d")
        self._stop_btn.config(state="normal", bg="#e74c3c")
        self._clear_log()

        self._append_log("===== 开始 RPA =====\n", "info")
        self._phase_label.config(text="阶段: 运行中", fg="#27ae60")

        self._worker_thread = threading.Thread(target=self._run_rpa, daemon=True)
        self._worker_thread.start()

    def _stop_rpa(self):
        self._stop_flag.set()
        self._append_log("===== 正在停止 RPA =====\n", "warning")
        self._stop_btn.config(state="disabled", bg="#7f8c8d")

    def _batch_process(self):
        """批量处理多个 SKU"""
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("提示", "RPA 正在运行中")
            return

        try:
            limit = int(self._batch_limit_var.get())
        except ValueError:
            messagebox.showerror("错误", "批量数量必须是整数")
            return

        self._stop_flag.clear()
        self._start_btn.config(state="disabled", bg="#7f8c8d")
        self._stop_btn.config(state="normal", bg="#e74c3c")
        self._clear_log()

        self._append_log(f"===== 批量处理开始 (数量: {limit}) =====\n", "info")
        self._phase_label.config(text="阶段: 批量处理中", fg="#27ae60")

        self._worker_thread = threading.Thread(
            target=self._run_batch, args=(limit,), daemon=True
        )
        self._worker_thread.start()

    def _run_rpa(self):
        """执行单个 SKU 的图搜 RPA"""
        try:
            sys.stdout = _QueueWriter(self._log_queue)
            sys.stderr = _QueueWriter(self._log_queue)

            # 1. 获取待处理 SKU
            row = self._get_pending_sku()
            if not row:
                self._log_queue.put("没有待图搜的 SKU\n")
                return

            sku_id, image_url = row
            self._log_queue.put(f"SKU ID: {sku_id}\n")
            self._log_queue.put(f"图片 URL: {image_url}\n")

            # 2. 获取筛选条件
            active_filters = [k for k, v in self._filter_vars.items() if v.get()]
            self._log_queue.put(f"筛选条件: {active_filters}\n")

            # 3. 执行图搜
            success = self._execute_image_search(sku_id, image_url, active_filters)

            if success:
                self._log_queue.put("===== RPA 完成 =====\n")
            else:
                self._log_queue.put("===== RPA 失败 =====\n")

        except Exception as e:
            self._log_queue.put(f"RPA 异常: {e}\n")
            traceback.print_exc()
        finally:
            sys.stdout = self._old_stdout
            sys.stderr = self._old_stderr
            self.root.after(0, self._rpa_finished)

    def _run_batch(self, limit: int):
        """批量执行图搜 RPA"""
        try:
            sys.stdout = _QueueWriter(self._log_queue)
            sys.stderr = _QueueWriter(self._log_queue)

            rows = self._get_pending_skus(limit)
            if not rows:
                self._log_queue.put("没有待图搜的 SKU\n")
                return

            self._log_queue.put(f"待处理 SKU 数量: {len(rows)}\n")

            active_filters = [k for k, v in self._filter_vars.items() if v.get()]
            success_count = 0
            fail_count = 0

            for i, (sku_id, image_url) in enumerate(rows):
                if self._stop_flag.is_set():
                    self._log_queue.put("用户停止了 RPA\n")
                    break

                self._log_queue.put(f"\n[{i+1}/{len(rows)}] 处理 SKU {sku_id}\n")
                self._progress_label.config(text=f"进度: {i+1}/{len(rows)}")

                try:
                    success = self._execute_image_search(sku_id, image_url, active_filters)
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    self._log_queue.put(f"SKU {sku_id} 异常: {e}\n")
                    fail_count += 1

            self._log_queue.put(f"\n===== 批量处理完成: 成功={success_count}, 失败={fail_count} =====\n")

        except Exception as e:
            self._log_queue.put(f"批量处理异常: {e}\n")
            traceback.print_exc()
        finally:
            sys.stdout = self._old_stdout
            sys.stderr = self._old_stderr
            self.root.after(0, self._rpa_finished)

    def _execute_image_search(self, sku_id: int, image_url: str, filters: list[str]) -> bool:
        """执行单次图搜 RPA"""
        captured = []

        try:
            pw = sync_playwright().start()
            browser = pw.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0] if browser.contexts else browser.new_context()

            def on_response(response):
                if IMAGE_SEARCH_API in response.url:
                    try:
                        body = response.json()
                        captured.append(body)
                        self._log_queue.put("捕获到图搜响应\n")
                    except Exception as e:
                        self._log_queue.put(f"解析响应失败: {e}\n")

            context.on("response", on_response)

            # 导航到采集页面
            page = context.new_page()
            target_url = self._url_var.get().strip() or DEFAULT_TARGET_URL
            self._log_queue.put(f"正在打开: {target_url}\n")
            page.goto(target_url, wait_until="networkidle", timeout=60000)
            self._log_queue.put(f"页面已加载: {page.title()}\n")

            # 等待 iframe 加载
            self._log_queue.put("等待 iframe 加载...\n")
            iframe = None
            for attempt in range(10):
                page.wait_for_timeout(2000)
                self._log_queue.put(f"检测 frames ({attempt+1}/10)...\n")
                for f in page.frames:
                    self._log_queue.put(f"  frame: {f.url[:80]}\n")
                    if "aibuy.1688.com" in f.url or "1688" in f.url:
                        iframe = f
                        self._log_queue.put(f"找到 1688 iframe: {f.url[:80]}\n")
                        break
                if iframe:
                    break

            if not iframe:
                self._log_queue.put("未找到 1688 iframe\n")
                return False

            # 执行图片搜索
            if not self._image_search(page, iframe, image_url):
                return False

            page.wait_for_timeout(5000)

            # 设置筛选条件
            if filters:
                self._select_filters(iframe, page, filters)

            # 等待响应
            page.wait_for_timeout(5000)

            if captured:
                resp = captured[-1]
                image_links, detail_urls = self._parse_response(resp)
                self._save_to_db(sku_id, image_links, detail_urls, resp)
                self._log_queue.put(
                    f"完成: {len(image_links)} 张图, {len(detail_urls)} 个链接\n"
                )
                return True
            else:
                self._log_queue.put("未捕获到响应\n")
                return False

        except Exception as e:
            self._log_queue.put(f"图搜执行失败: {e}\n")
            return False
        finally:
            try:
                pw.stop()
            except Exception:
                pass

    def _image_search(self, page, iframe, image_url: str) -> bool:
        """在iframe弹窗中执行图片搜索"""
        try:
            iframe.locator("text=图片链接搜索").first.click()
            self._log_queue.put("已点击图片链接搜索\n")
            page.wait_for_timeout(1000)
        except Exception as e:
            self._log_queue.put(f"点击图片链接搜索失败: {e}\n")
            return False

        try:
            textarea = iframe.locator("textarea").first
            textarea.wait_for(state="visible", timeout=5000)
            textarea.fill(image_url)
            self._log_queue.put(f"已填入图片链接\n")
            page.wait_for_timeout(500)
        except Exception as e:
            self._log_queue.put(f"填入图片链接失败: {e}\n")
            return False

        try:
            iframe.locator('span:has-text("确定")').first.click()
            self._log_queue.put("已点击确定\n")
            page.wait_for_timeout(3000)
        except Exception as e:
            self._log_queue.put(f"点击确定失败: {e}\n")
            return False

        return True

    def _select_filters(self, iframe, page, filters: list[str]):
        """在图搜结果中多选筛选条件"""
        try:
            dropdowns = iframe.locator('[class*="select"]').all()
            for dropdown in dropdowns:
                text = dropdown.text_content() or ""
                if "商品信息" in text or "请选择" in text:
                    dropdown.click()
                    page.wait_for_timeout(500)
                    self._log_queue.put("已打开商品信息下拉框\n")
                    break

            for text in filters:
                try:
                    iframe.locator(f"text={text}").first.click()
                    self._log_queue.put(f"已勾选: {text}\n")
                    page.wait_for_timeout(300)
                except Exception as e:
                    self._log_queue.put(f"勾选 {text} 失败: {e}\n")

            try:
                iframe.locator("text=图搜结果").first.click()
                page.wait_for_timeout(500)
            except Exception:
                pass
        except Exception as e:
            self._log_queue.put(f"设置筛选条件失败: {e}\n")

    # ========== 数据库操作 ==========

    def _get_pending_sku(self):
        conn = pymysql.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT sku, main_image_url FROM sku_products "
                    "WHERE main_image_url IS NOT NULL "
                    "AND alibaba_image_search_response IS NULL "
                    "LIMIT 1"
                )
                return cursor.fetchone()
        finally:
            conn.close()

    def _get_pending_skus(self, limit: int):
        conn = pymysql.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT sku, main_image_url FROM sku_products "
                    "WHERE main_image_url IS NOT NULL "
                    "AND alibaba_image_search_response IS NULL "
                    "LIMIT %s",
                    (limit,),
                )
                return cursor.fetchall()
        finally:
            conn.close()

    def _parse_response(self, response_data):
        image_links = []
        detail_urls = []
        try:
            items = response_data.get("data", {}).get("data", [])
            for item in items:
                pic_url = item.get("imageUrl", "")
                detail_url = item.get("offerDetailUrl", "") or item.get("link", "")
                item_id = item.get("itemId", "")
                if pic_url and detail_url:
                    image_links.append({
                        "picurl": pic_url,
                        "1688url": detail_url,
                        "itemId": item_id,
                    })
                    detail_urls.append(detail_url)
        except Exception as e:
            self._log_queue.put(f"解析响应失败: {e}\n")
        return image_links, detail_urls

    def _save_to_db(self, sku_id, image_links, detail_urls, response_data):
        conn = pymysql.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE sku_products SET "
                    "alibaba_image_links = %s, "
                    "alibaba_detail_urls = %s, "
                    "alibaba_image_search_response = %s "
                    "WHERE sku = %s",
                    (
                        json.dumps(image_links, ensure_ascii=False),
                        json.dumps(detail_urls, ensure_ascii=False),
                        json.dumps(response_data, ensure_ascii=False),
                        sku_id,
                    ),
                )
            conn.commit()
            self._log_queue.put(
                f"已写入数据库: sku={sku_id}, {len(image_links)} 张图\n"
            )
        except Exception as e:
            self._log_queue.put(f"写入数据库失败: {e}\n")
            conn.rollback()
        finally:
            conn.close()

    # ========== 工具方法 ==========

    @staticmethod
    def _detect_chrome() -> str | None:
        """检测 Chrome 可执行文件路径"""
        possible = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in possible:
            if os.path.exists(p):
                return p

        import shutil
        for name in ["chrome", "google-chrome", "chromium"]:
            found = shutil.which(name)
            if found:
                return found
        return None

    def _rpa_finished(self):
        self._append_log("\n===== RPA 已结束 =====\n", "success")
        self._phase_label.config(text="阶段: 已完成", fg="#2c3e50")
        self._start_btn.config(state="normal", bg="#27ae60")
        self._stop_btn.config(state="disabled", bg="#7f8c8d")
        self._stop_flag.clear()
        self._worker_thread = None

    def _poll_status(self):
        def check():
            info = {"cdp": "未连接", "db": "未连接", "pending": 0}
            try:
                pw = sync_playwright().start()
                browser = pw.chromium.connect_over_cdp(CDP_URL)
                pages = browser.contexts[0].pages if browser.contexts else []
                info["cdp"] = f"已连接 ({len(pages)} 页)"
                browser.close()
                pw.stop()
            except Exception:
                info["cdp"] = "未连接"

            try:
                conn = pymysql.connect(**DB_CONFIG)
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(*) AS cnt FROM sku_products "
                        "WHERE main_image_url IS NOT NULL "
                        "AND alibaba_image_search_response IS NULL"
                    )
                    info["pending"] = cursor.fetchone()[0]
                conn.close()
                info["db"] = "已连接"
            except Exception:
                info["db"] = "未连接"

            self.root.after(0, lambda: self._set_status(info))
            self.root.after(10000, self._poll_status)

        threading.Thread(target=check, daemon=True).start()

    def _set_status(self, info: dict):
        cdp_text = info.get("cdp", "")
        cdp_color = "#27ae60" if "已连接" in cdp_text else "#e74c3c"
        self._cdp_label.config(text=f"CDP: {cdp_text}", fg=cdp_color)

        db_text = info.get("db", "")
        db_color = "#27ae60" if "已连接" in db_text else "#e74c3c"
        self._db_label.config(text=f"DB: {db_text}", fg=db_color)

        self._pending_label.config(text=f"待处理: {info.get('pending', 0)}", fg="#2c3e50")

    # ========== 日志 ==========

    def _start_log_poller(self):
        while True:
            try:
                text = self._log_queue.get_nowait()
                self._append_log(text)
            except queue.Empty:
                break
        self.root.after(200, self._start_log_poller)

    def _append_log(self, text: str, tag: str | None = None):
        try:
            self._log_area.configure(state="normal")
            if tag:
                self._log_area.insert("end", text, tag)
            else:
                self._log_area.insert("end", text)

            line_count = int(self._log_area.index("end-1c").split(".")[0])
            if line_count > 5000:
                self._log_area.delete("1.0", f"{line_count - 5000}.0")

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

    # ========== 配置 ==========

    def _save_config(self):
        try:
            config = {
                "url": self._url_var.get(),
                "batch_limit": self._batch_limit_var.get(),
                "filters": {k: v.get() for k, v in self._filter_vars.items()},
            }
            with open(GUI_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("成功", "配置已保存！")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def _load_config(self):
        if not os.path.exists(GUI_CONFIG_FILE):
            return
        try:
            with open(GUI_CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)

            if "url" in config:
                self._url_var.set(config["url"])
            if "batch_limit" in config:
                self._batch_limit_var.set(config["batch_limit"])
            if "filters" in config:
                for k, v in config["filters"].items():
                    if k in self._filter_vars:
                        self._filter_vars[k].set(v)
        except Exception:
            pass

    def _on_close(self):
        if self._worker_thread and self._worker_thread.is_alive():
            if not messagebox.askyesno("确认退出", "RPA 正在运行中，确定退出吗？"):
                return
            self._stop_rpa()
        self.root.destroy()


class _QueueWriter:
    """将 print 输出重定向到队列"""
    def __init__(self, q: queue.Queue):
        self._queue = q

    def write(self, text: str):
        if text:
            self._queue.put(text)

    def flush(self):
        pass


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
