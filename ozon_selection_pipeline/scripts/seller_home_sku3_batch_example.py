from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from ozon_pipeline.browser_ozon import BrowserOzonClient
from ozon_pipeline.config import ROOT_DIR, settings


# 全局数组：统一存放所有成功获取的合法 SKU 数据。
GLOBAL_SKU3_DATA: list[dict[str, Any]] = []


def log_line(*parts: Any) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{timestamp}]", *parts)


def extract_sku_from_product_url(url: str) -> str | None:
    if not url:
        return None
    path = url.split("?", 1)[0].rstrip("/")
    segment = path.split("/")[-1]
    parts = segment.split("-")
    candidates = [part for part in parts if part.isdigit() and len(part) >= 6]
    if candidates:
        return candidates[-1]
    return None


@dataclass(frozen=True)
class Sku3BatchRequest:
    # 批次请求对象：统一约束每一批次的批次号、SKU 数组、时间戳等必填字段。
    batch_no: int
    total_batches: int
    sku_ids: list[str]
    request_ts_ms: int
    seller_url: str

    def validate(self, *, max_batch_size: int, max_batches: int) -> None:
        if self.batch_no < 1 or self.batch_no > self.total_batches:
            raise ValueError(f"非法批次号: batch_no={self.batch_no}, total_batches={self.total_batches}")
        if self.total_batches < 1 or self.total_batches > max_batches:
            raise ValueError(f"批次数超限: total_batches={self.total_batches}, max_batches={max_batches}")
        if not self.sku_ids:
            raise ValueError("当前批次 SKU 数组不能为空")
        if len(self.sku_ids) > max_batch_size:
            raise ValueError(
                f"当前批次 SKU 数量超限: batch_no={self.batch_no}, size={len(self.sku_ids)}, max={max_batch_size}"
            )
        if len(set(self.sku_ids)) != len(self.sku_ids):
            raise ValueError(f"当前批次存在重复 SKU: batch_no={self.batch_no}")
        if any(not str(sku).strip() for sku in self.sku_ids):
            raise ValueError(f"当前批次存在空 SKU: batch_no={self.batch_no}")
        if not self.seller_url.strip():
            raise ValueError("seller_url 不能为空")
        if self.request_ts_ms <= 0:
            raise ValueError("request_ts_ms 必须为正整数")

    def to_context_payload(self) -> dict[str, Any]:
        # 说明：
        # 当前工程内对 sku3 的底层 HTTP 调用已经固化在 BrowserOzonClient.top_list_sku3_batch()。
        # 这里保留批次号、SKU 数组、请求时间戳等字段，作为调度层的标准化请求上下文，
        # 避免擅自改写底层已验证的接口格式。
        return {
            "batch_no": self.batch_no,
            "total_batches": self.total_batches,
            "sku_ids": list(self.sku_ids),
            "request_ts_ms": self.request_ts_ms,
            "seller_url": self.seller_url,
        }


@dataclass
class BatchExecutionResult:
    request: Sku3BatchRequest
    data: list[dict[str, Any]]
    elapsed_ms: int
    retry_used: int = 0


@dataclass
class RetryRecord:
    batch_no: int
    retry_index: int
    reason: str
    happened_at: str


@dataclass
class CollectionSummary:
    global_store: list[dict[str, Any]]
    start_perf: float = field(default_factory=time.perf_counter)
    success_batches: int = 0
    failed_retry_count: int = 0
    longest_batch_ms: int = 0
    valid_sku_total: int = 0
    retry_logs: list[RetryRecord] = field(default_factory=list)

    def record_success(self, result: BatchExecutionResult) -> None:
        self.success_batches += 1
        self.longest_batch_ms = max(self.longest_batch_ms, result.elapsed_ms)
        self.valid_sku_total += len(result.data)
        self.global_store.extend(result.data)

    def record_retry(self, batch_no: int, retry_index: int, reason: str) -> None:
        self.failed_retry_count += 1
        self.retry_logs.append(
            RetryRecord(
                batch_no=batch_no,
                retry_index=retry_index,
                reason=reason,
                happened_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            )
        )

    def build_report(self) -> dict[str, Any]:
        total_elapsed_ms = int((time.perf_counter() - self.start_perf) * 1000)
        return {
            "metrics": {
                "total_elapsed_ms": total_elapsed_ms,
                "success_batches": self.success_batches,
                "failed_retry_count": self.failed_retry_count,
                "longest_batch_ms": self.longest_batch_ms,
                "valid_sku_total": self.valid_sku_total,
            },
            "retry_logs": [
                {
                    "batch_no": item.batch_no,
                    "retry_index": item.retry_index,
                    "reason": item.reason,
                    "happened_at": item.happened_at,
                }
                for item in self.retry_logs
            ],
            "data": self.global_store,
        }


class BatchRequestError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class PartialBatchResultError(BatchRequestError):
    pass


# 模块 ①：SKU 批次拆分模块。
def split_sku_batches(sku_ids: list[str], *, batch_size: int = 40, max_batches: int = 20) -> list[list[str]]:
    normalized: list[str] = []
    seen: set[str] = set()
    for sku in sku_ids:
        text = str(sku).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)

    if not normalized:
        raise ValueError("SKU 列表为空，无法拆分批次")
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")
    if max_batches <= 0:
        raise ValueError("max_batches 必须大于 0")
    if len(normalized) > batch_size * max_batches:
        raise ValueError(
            f"SKU 总数超出最大可处理上限: total={len(normalized)}, limit={batch_size * max_batches}"
        )

    batches: list[list[str]] = []
    for index in range(0, len(normalized), batch_size):
        batch = normalized[index : index + batch_size]
        if not batch:
            continue
        batches.append(batch)

    if len(batches) > max_batches:
        raise ValueError(f"实际批次数超限: total_batches={len(batches)}, max_batches={max_batches}")
    return batches


# 模块 ②：sku3 接口请求模块。
def request_sku3_batch(
    browser: BrowserOzonClient,
    *,
    seller_url: str,
    batch_no: int,
    total_batches: int,
    sku_ids: list[str],
    concurrency: int,
    max_batch_size: int = 40,
    max_batches: int = 20,
) -> BatchExecutionResult:
    request = Sku3BatchRequest(
        batch_no=batch_no,
        total_batches=total_batches,
        sku_ids=list(sku_ids),
        request_ts_ms=time.time_ns() // 1_000_000,
        seller_url=seller_url,
    )
    request.validate(max_batch_size=max_batch_size, max_batches=max_batches)

    context_payload = request.to_context_payload()
    log_line(
        "准备发起 sku3 批次请求:",
        f"batch={context_payload['batch_no']}/{context_payload['total_batches']}",
        f"size={len(context_payload['sku_ids'])}",
        f"request_ts_ms={context_payload['request_ts_ms']}",
    )

    started = time.perf_counter()
    try:
        raw = browser.top_list_sku3_batch(context_payload["sku_ids"], concurrency=concurrency)
    except Exception as exc:
        raise classify_request_exception(exc, batch_no=batch_no) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if not isinstance(raw, dict):
        raise BatchRequestError(f"批次 {batch_no} 返回结果不是 dict", retryable=False)

    missing = [sku for sku in context_payload["sku_ids"] if sku not in raw]
    if missing:
        preview = ",".join(missing[:5])
        raise PartialBatchResultError(
            f"批次 {batch_no} 返回不完整，缺失 {len(missing)} 个 SKU，示例: {preview}",
            retryable=True,
        )

    batch_rows: list[dict[str, Any]] = []
    for sku in context_payload["sku_ids"]:
        payload = raw.get(sku)
        if not isinstance(payload, dict) or not payload:
            raise BatchRequestError(f"批次 {batch_no} 的 SKU {sku} 返回空数据", retryable=True)
        batch_rows.append(
            {
                "batch_no": context_payload["batch_no"],
                "total_batches": context_payload["total_batches"],
                "request_ts_ms": context_payload["request_ts_ms"],
                "seller_url": context_payload["seller_url"],
                "sku": sku,
                "sku3": payload,
            }
        )

    return BatchExecutionResult(request=request, data=batch_rows, elapsed_ms=elapsed_ms)


# 模块 ③：通用延时控制模块。
def precise_delay(seconds: float = 0.5, *, tolerance_ms: int = 50) -> int:
    if seconds < 0:
        raise ValueError("seconds 不能为负数")

    target = time.perf_counter() + seconds
    coarse_sleep = max(0.0, seconds - 0.02)
    if coarse_sleep > 0:
        time.sleep(coarse_sleep)

    while True:
        now = time.perf_counter()
        if now >= target:
            break
        remaining = target - now
        time.sleep(min(0.005, remaining))

    actual_ms = int((time.perf_counter() - (target - seconds)) * 1000)
    drift_ms = abs(actual_ms - int(seconds * 1000))
    if drift_ms > tolerance_ms:
        log_line(
            "延时控制告警:",
            f"target_ms={int(seconds * 1000)}",
            f"actual_ms={actual_ms}",
            f"drift_ms={drift_ms}",
        )
    return actual_ms


def classify_request_exception(exc: Exception, *, batch_no: int) -> BatchRequestError:
    text = str(exc).strip()
    lowered = text.lower()

    if "429" in lowered:
        return BatchRequestError(f"批次 {batch_no} 触发 429 限流: {text}", retryable=True, status_code=429)
    if any(code in lowered for code in ("500", "502", "503", "504")):
        return BatchRequestError(f"批次 {batch_no} 触发 5xx 服务端错误: {text}", retryable=True)
    if any(keyword in lowered for keyword in ("timeout", "timed out", "abort", "connection", "network", "econn")):
        return BatchRequestError(f"批次 {batch_no} 网络或超时错误: {text}", retryable=True)
    if any(keyword in lowered for keyword in ("cloudflare challenge", "manual verification is required")):
        return BatchRequestError(f"批次 {batch_no} 需要人工介入: {text}", retryable=False)
    if any(code in lowered for code in ("400", "401", "403", "404", "422")):
        return BatchRequestError(f"批次 {batch_no} 入参或权限错误: {text}", retryable=False)
    return BatchRequestError(f"批次 {batch_no} 未分类异常: {text}", retryable=True)


# 模块 ④：异常重试模块。
def execute_with_retry(
    action: Callable[[], BatchExecutionResult],
    *,
    batch_no: int,
    summary: CollectionSummary,
    max_retries: int = 3,
    retry_delay_seconds: float = 0.5,
) -> BatchExecutionResult:
    retry_index = 0
    while True:
        try:
            result = action()
            result.retry_used = retry_index
            return result
        except BatchRequestError as exc:
            if retry_index >= max_retries or not exc.retryable:
                raise
            retry_index += 1
            summary.record_retry(batch_no=batch_no, retry_index=retry_index, reason=str(exc))
            log_line(
                "批次重试中:",
                f"batch={batch_no}",
                f"retry={retry_index}/{max_retries}",
                f"reason={exc}",
            )
            precise_delay(retry_delay_seconds)


# 模块 ⑤：数据汇总与指标统计模块。
def dump_summary_to_json(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_seller_home_skus(
    browser: BrowserOzonClient,
    seller_url: str,
    *,
    expected_skus: int,
    max_scrolls: int,
) -> list[str]:
    result = browser.seller_home_products(seller_url, max_scrolls=max_scrolls)
    items = result.get("items") or []
    unique_skus: list[str] = []
    seen: set[str] = set()

    for item in items:
        sku = str(item.get("sku") or "").strip()
        if not sku:
            sku = str(
                extract_sku_from_product_url(str(item.get("href") or item.get("product_url") or "")) or ""
            ).strip()
        if not sku or sku in seen:
            continue
        seen.add(sku)
        unique_skus.append(sku)
        if len(unique_skus) >= expected_skus:
            break

    if len(unique_skus) < expected_skus:
        raise RuntimeError(
            f"卖家主页唯一 SKU 数不足 {expected_skus} 个，当前仅抓到 {len(unique_skus)} 个，无法执行完整 800 SKU 批量采集"
        )
    return unique_skus[:expected_skus]


def create_browser_client() -> BrowserOzonClient:
    return BrowserOzonClient(
        profile_dir=settings.chrome_profile_dir,
        extension_dir=settings.chrome_extension_dir,
        executable_path=settings.chrome_executable_path or None,
        channel=settings.chrome_channel or None,
        proxy_server=settings.chrome_proxy_server or None,
        cdp_url=settings.chrome_cdp_url or None,
        remote_debugging_port=settings.chrome_remote_debugging_port,
        headless=settings.chrome_headless,
    )


def run_collection(args: argparse.Namespace) -> dict[str, Any]:
    GLOBAL_SKU3_DATA.clear()
    browser = create_browser_client()
    summary = CollectionSummary(global_store=GLOBAL_SKU3_DATA)

    with browser.session():
        sku_ids = fetch_seller_home_skus(
            browser,
            args.seller_url,
            expected_skus=args.expected_skus,
            max_scrolls=args.max_scrolls,
        )
        log_line("卖家主页 SKU 抓取完成:", f"seller={args.seller_url}", f"sku_total={len(sku_ids)}")

        batches = split_sku_batches(
            sku_ids,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
        log_line("SKU 批次拆分完成:", f"total_batches={len(batches)}", f"batch_size={args.batch_size}")

        total_batches = len(batches)
        for index, batch_skus in enumerate(batches, start=1):
            result = execute_with_retry(
                lambda batch_no=index, skus=batch_skus: request_sku3_batch(
                    browser,
                    seller_url=args.seller_url,
                    batch_no=batch_no,
                    total_batches=total_batches,
                    sku_ids=skus,
                    concurrency=args.concurrency,
                    max_batch_size=args.batch_size,
                    max_batches=args.max_batches,
                ),
                batch_no=index,
                summary=summary,
                max_retries=args.max_retries,
                retry_delay_seconds=args.delay_seconds,
            )
            summary.record_success(result)
            log_line(
                "批次完成:",
                f"batch={index}/{total_batches}",
                f"batch_size={len(batch_skus)}",
                f"elapsed_ms={result.elapsed_ms}",
                f"retry_used={result.retry_used}",
            )
            if index < total_batches:
                actual_sleep_ms = precise_delay(args.delay_seconds)
                log_line("批次间固定延时完成:", f"batch={index}", f"sleep_ms={actual_sleep_ms}")

    report = summary.build_report()
    output_path = Path(args.output_json).resolve()
    dump_summary_to_json(report, output_path)
    log_line("采集完成，结果已输出:", str(output_path))
    log_line("核心指标:", json.dumps(report["metrics"], ensure_ascii=False))
    return report


def run_self_check() -> None:
    log_line("开始执行 self-check")

    sample_800 = [str(3100000000 + i) for i in range(800)]
    batches = split_sku_batches(sample_800, batch_size=40, max_batches=20)
    assert len(batches) == 20, f"800 SKU 应拆分为 20 批，实际 {len(batches)} 批"
    assert all(len(batch) == 40 for batch in batches), "800 SKU 场景下每批都应为 40"

    sample_tail = [str(3200000000 + i) for i in range(783)]
    tail_batches = split_sku_batches(sample_tail, batch_size=40, max_batches=20)
    assert len(tail_batches) == 20, f"783 SKU 应仍为 20 批，实际 {len(tail_batches)} 批"
    assert len(tail_batches[-1]) == 23, f"尾批应为 23，实际 {len(tail_batches[-1])}"

    slept_ms = precise_delay(0.5)
    assert 450 <= slept_ms <= 550, f"0.5 秒延时应在容差内，实际 {slept_ms}ms"

    summary = CollectionSummary(global_store=[])
    attempts = {"count": 0}

    def flaky_action() -> BatchExecutionResult:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise BatchRequestError("模拟 503 错误", retryable=True, status_code=503)
        request = Sku3BatchRequest(
            batch_no=1,
            total_batches=1,
            sku_ids=["300000001"],
            request_ts_ms=time.time_ns() // 1_000_000,
            seller_url="https://www.ozon.ru/seller/demo/",
        )
        return BatchExecutionResult(
            request=request,
            data=[
                {
                    "batch_no": 1,
                    "total_batches": 1,
                    "request_ts_ms": request.request_ts_ms,
                    "seller_url": request.seller_url,
                    "sku": "300000001",
                    "sku3": {"demo": True},
                }
            ],
            elapsed_ms=123,
        )

    result = execute_with_retry(
        flaky_action,
        batch_no=1,
        summary=summary,
        max_retries=3,
        retry_delay_seconds=0.5,
    )
    summary.record_success(result)
    report = summary.build_report()

    assert attempts["count"] == 3, f"应在第 3 次尝试成功，实际 attempts={attempts['count']}"
    assert report["metrics"]["success_batches"] == 1, "成功批次数应为 1"
    assert report["metrics"]["failed_retry_count"] == 2, "累计失败重试次数应为 2"
    assert report["metrics"]["valid_sku_total"] == 1, "有效 SKU 总数应为 1"

    log_line("self-check 通过")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从卖家主页抓取 800 个 SKU，并按每批 40 个调用 sku3 接口的完整示例。"
    )
    parser.add_argument("--seller-url", default="", help="卖家主页 URL，例如 https://www.ozon.ru/seller/angelcity-1445659/")
    parser.add_argument("--expected-skus", type=int, default=800, help="期望抓取的唯一 SKU 数量，默认 800")
    parser.add_argument("--batch-size", type=int, default=40, help="每批最大 SKU 数，默认 40")
    parser.add_argument("--max-batches", type=int, default=20, help="最大批次数，默认 20")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.top_list_sku3_batch_concurrency,
        help="批内并发度，默认读取 TOP_LIST_SKU3_BATCH_CONCURRENCY",
    )
    parser.add_argument("--max-retries", type=int, default=3, help="单批次最大重试次数，默认 3")
    parser.add_argument("--delay-seconds", type=float, default=0.5, help="批次间与重试前固定延时，默认 0.5 秒")
    parser.add_argument("--max-scrolls", type=int, default=8, help="卖家主页 DOM 回退抓取时的滚动次数")
    parser.add_argument(
        "--output-json",
        default=str(ROOT_DIR / "diagnostics" / "seller_home_sku3_batch_result.json"),
        help="采集结果输出 JSON 路径",
    )
    parser.add_argument("--self-check", action="store_true", help="仅执行模块级自检，不访问线上接口")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.self_check:
        run_self_check()
        return

    if not args.seller_url.strip():
        raise SystemExit("错误: 未提供 --seller-url")
    if args.expected_skus != 800:
        log_line("提示: 当前示例按 800 SKU 设计，若修改 expected_skus，请同步确认批次约束仍满足要求")
    if args.batch_size != 40:
        raise SystemExit("错误: 本示例按需求固定每批最多 40 个 SKU，请使用 --batch-size 40")
    if args.max_batches != 20:
        raise SystemExit("错误: 本示例按需求固定最大 20 批，请使用 --max-batches 20")

    run_collection(args)


if __name__ == "__main__":
    main()
