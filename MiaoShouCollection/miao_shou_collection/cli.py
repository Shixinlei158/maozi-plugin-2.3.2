from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from typing import Any

from .browser import BrowserClient
from .config import settings


def log_line(*parts: Any, prefix: str = "log") -> None:
    """输出日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{prefix}]", *parts)


def cmd_launch_chrome(args: argparse.Namespace) -> None:
    """启动Chrome浏览器命令"""
    client = BrowserClient(
        profile_dir=args.profile_dir,
        extension_dir=args.extension_dir,
        executable_path=args.chrome_exe,
        channel=args.channel,
        proxy_server=args.proxy,
        remote_debugging_port=args.port,
        headless=args.headless,
    )
    
    url = args.url or "about:blank"
    log_line(f"启动Chrome浏览器，URL: {url}")
    
    try:
        client.launch_real_chrome(url)
        log_line("Chrome浏览器启动成功")
        
        if args.wait:
            log_line("等待浏览器关闭...")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                log_line("收到中断信号，停止等待")
    except Exception as e:
        log_line(f"启动Chrome浏览器失败: {e}", prefix="error")
        sys.exit(1)


def cmd_connect_chrome(args: argparse.Namespace) -> None:
    """连接到现有Chrome浏览器命令"""
    client = BrowserClient(
        cdp_url=args.cdp_url,
        remote_debugging_port=args.port,
    )
    
    log_line(f"连接到Chrome浏览器，CDP: {args.cdp_url or f'http://127.0.0.1:{args.port}'}")
    
    try:
        with client:
            log_line("成功连接到Chrome浏览器")
            
            if args.url:
                log_line(f"导航到: {args.url}")
                page = client.get_page(args.url)
                log_line(f"页面标题: {page.title()}")
            
            if args.interactive:
                log_line("进入交互模式，按Ctrl+C退出")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    log_line("收到中断信号，退出交互模式")
    except Exception as e:
        log_line(f"连接Chrome浏览器失败: {e}", prefix="error")
        sys.exit(1)


def cmd_show_config(args: argparse.Namespace) -> None:
    """显示配置命令"""
    print("当前浏览器配置:")
    print(f"  Chrome配置目录: {settings.chrome_profile_dir}")
    print(f"  扩展目录: {settings.chrome_extension_dir}")
    print(f"  扩展ID: {settings.chrome_extension_id}")
    print(f"  Chrome可执行文件: {settings.chrome_executable_path or '自动检测'}")
    print(f"  Chrome通道: {settings.chrome_channel}")
    print(f"  代理服务器: {settings.chrome_proxy_server or '无'}")
    print(f"  CDP URL: {settings.chrome_cdp_url or '无'}")
    print(f"  远程调试端口: {settings.chrome_remote_debugging_port}")
    print(f"  无头模式: {settings.chrome_headless}")
    print(f"  指纹伪装: {'启用' if settings.chrome_fingerprint_mask_enabled else '禁用'}")
    
    if settings.chrome_fingerprint_mask_enabled:
        print(f"  用户代理: {settings.chrome_fingerprint_user_agent or '默认'}")
        print(f"  平台: {settings.chrome_fingerprint_platform}")
        print(f"  语言: {settings.chrome_fingerprint_locale}")
        print(f"  屏幕分辨率: {settings.chrome_fingerprint_screen_width}x{settings.chrome_fingerprint_screen_height}")


def main() -> None:
    """主函数"""
    parser = argparse.ArgumentParser(description="MiaoShouCollection - 妙手采集浏览器控制工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # 启动Chrome命令
    launch_parser = subparsers.add_parser("launch", help="启动Chrome浏览器")
    launch_parser.add_argument("--url", help="启动后导航到的URL")
    launch_parser.add_argument("--profile-dir", help="Chrome配置目录")
    launch_parser.add_argument("--extension-dir", help="扩展目录")
    launch_parser.add_argument("--chrome-exe", help="Chrome可执行文件路径")
    launch_parser.add_argument("--channel", help="Chrome通道")
    launch_parser.add_argument("--proxy", help="代理服务器")
    launch_parser.add_argument("--port", type=int, default=settings.chrome_remote_debugging_port, help="远程调试端口")
    launch_parser.add_argument("--headless", action="store_true", help="无头模式")
    launch_parser.add_argument("--wait", action="store_true", help="等待浏览器关闭")
    launch_parser.set_defaults(func=cmd_launch_chrome)
    
    # 连接Chrome命令
    connect_parser = subparsers.add_parser("connect", help="连接到现有Chrome浏览器")
    connect_parser.add_argument("--cdp-url", help="CDP URL")
    connect_parser.add_argument("--port", type=int, default=settings.chrome_remote_debugging_port, help="远程调试端口")
    connect_parser.add_argument("--url", help="导航到指定URL")
    connect_parser.add_argument("--interactive", action="store_true", help="交互模式")
    connect_parser.set_defaults(func=cmd_connect_chrome)
    
    # 显示配置命令
    config_parser = subparsers.add_parser("config", help="显示当前配置")
    config_parser.set_defaults(func=cmd_show_config)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()