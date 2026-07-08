# -*- coding: utf-8 -*-
"""market.py —— 币安公开行情 REST 拉价格/成交量,price-in 判定(T-008)。

对应 pipeline 第 3 层交叉验证(design.md「方案概述」/AC-006):
双模型判断一致后,拉取币安公开行情,若目标资产在信号方向上短窗口内已经
明显异动(价格已经涨/跌过配置阈值),视为消息已被市场消化(price-in),否决信号。

只用标准库 urllib 调币安公开接口,无需 API key:
- GET /api/v3/ticker/24hr?symbol=BTCUSDT   —— 当前价、24h 涨跌幅、24h 成交量
- GET /api/v3/klines?symbol=...&interval=1m&limit=N —— 近期 1 分钟 K 线,
  用窗口首尾收盘价算出「短窗口涨跌幅」,即 price-in 判定的核心依据。

fail-closed 原则(与 dedup/factcheck 的保守策略一致,详见 design.md「事实核查」段与
本任务描述):行情接口重试耗尽仍失败,或资产没有可映射的交易对时,
不能既没验证又放行信号,统一判为否决(priced_in=True),并在快照里注明
`marketAvailable: False` + `reason`,由编排层(app.py)据此落库 PRICED_IN
并可选择发系统异常告警,而不是误发未经行情验证的信号。
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量与配置
# ---------------------------------------------------------------------------

BINANCE_BASE_URL = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com")

DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("MARKET_TIMEOUT_SECONDS", "10"))
DEFAULT_MAX_RETRIES = int(os.environ.get("MARKET_MAX_RETRIES", "3"))
BACKOFF_BASE_SECONDS = float(os.environ.get("MARKET_BACKOFF_BASE_SECONDS", "1"))

# 短窗口:price-in 判定看「最近 N 分钟」的价格变动(1 分钟 K 线 × N 根)。
# 信号本身来自「刚发生的新帖」,只关心发帖后市场是否已抢跑,不需要更长窗口。
SHORT_WINDOW_MINUTES = int(os.environ.get("MARKET_SHORT_WINDOW_MINUTES", "15"))

# 资产符号 → 币安交易对映射;默认拼 USDT 现货对,覆盖不了的(非 USDT 对/新币)
# 可通过环境变量 MARKET_SYMBOL_OVERRIDES 传 JSON 覆盖或新增,如
# '{"USDT_ONLY_COIN": "USDTONLYCOINUSDT"}',不必改代码重新部署。
_DEFAULT_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "DOGE": "DOGEUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "BNB": "BNBUSDT",
    "ADA": "ADAUSDT",
    "TRUMP": "TRUMPUSDT",
}


def _load_symbol_overrides() -> dict:
    raw = os.environ.get("MARKET_SYMBOL_OVERRIDES", "")
    if not raw:
        return {}
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("market MARKET_SYMBOL_OVERRIDES 不是合法 JSON,忽略")
        return {}
    if not isinstance(overrides, dict):
        logger.warning("market MARKET_SYMBOL_OVERRIDES 不是对象,忽略")
        return {}
    return {str(k).upper(): str(v).upper() for k, v in overrides.items()}


_SYMBOL_MAP = {**_DEFAULT_SYMBOL_MAP, **_load_symbol_overrides()}


class MarketError(Exception):
    """行情接口重试耗尽仍失败,或响应结构异常。"""


def symbol_for_asset(asset: str) -> str | None:
    """资产代码 → 币安交易对;映射不到时返回 None(由调用方按 fail-closed 处理)。"""
    if not isinstance(asset, str) or not asset:
        return None
    return _SYMBOL_MAP.get(asset.strip().upper())


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------


def get_snapshot(asset: str, direction: str) -> dict:
    """拉取目标资产行情并做 price-in 判定,返回行情快照 dict。

    返回结构(始终包含全部字段,便于 notifier 直接引用、store 直接序列化落库):
    {
        "asset": str,                     原始资产代码(如 "BTC")
        "symbol": str | None,             映射到的币安交易对,映射失败为 None
        "direction": str,                 信号方向(bullish/bearish/其他)
        "marketAvailable": bool,          行情是否成功拿到(False = fail-closed 触发)
        "currentPrice": float | None,
        "change24hPct": float | None,     币安原生 24h 涨跌幅(百分比数值,如 2.35 表示 +2.35%)
        "shortWindowChangePct": float | None,  近 SHORT_WINDOW_MINUTES 分钟涨跌幅(百分比数值)
        "shortWindowMinutes": int,
        "volume24h": float | None,        24h 成交量(基础资产数量)
        "pricedIn": bool,                 price-in 判定结果(True = 否决)
        "reason": str,                    判定依据的简短说明,直接可用于日志/否决记录
    }

    price-in 判定规则(design.md AC-006 + 本任务约定的阈值 config.PRICE_IN_THRESHOLD_PCT):
    - 方向为 bullish:短窗口涨幅 ≥ 阈值 → 已被市场消化,否决
    - 方向为 bearish:短窗口跌幅(绝对值)≥ 阈值 → 已被市场消化,否决
    - 其余情况(含拿不到行情、资产无法映射交易对、方向不明)一律 fail-closed 否决,
      不放行没有经过行情验证的信号。
    """
    symbol = symbol_for_asset(asset)
    if symbol is None:
        logger.warning("market 资产 %r 无法映射到币安交易对,fail-closed 否决", asset)
        return _snapshot(
            asset=asset,
            symbol=None,
            direction=direction,
            market_available=False,
            current_price=None,
            change_24h_pct=None,
            short_window_change_pct=None,
            volume_24h=None,
            priced_in=True,
            reason=f"资产 {asset} 无可用交易对映射,fail-closed 否决",
        )

    try:
        ticker = _fetch_ticker_24hr(symbol)
        short_window_change_pct = _fetch_short_window_change_pct(symbol)
    except MarketError as exc:
        logger.error("market 行情接口最终失败 symbol=%s: %s,fail-closed 否决", symbol, exc)
        return _snapshot(
            asset=asset,
            symbol=symbol,
            direction=direction,
            market_available=False,
            current_price=None,
            change_24h_pct=None,
            short_window_change_pct=None,
            volume_24h=None,
            priced_in=True,
            reason=f"行情接口重试耗尽仍失败: {exc},fail-closed 否决",
        )

    current_price = ticker["current_price"]
    change_24h_pct = ticker["change_24h_pct"]
    volume_24h = ticker["volume_24h"]

    priced_in, reason = _judge_priced_in(direction, short_window_change_pct)

    return _snapshot(
        asset=asset,
        symbol=symbol,
        direction=direction,
        market_available=True,
        current_price=current_price,
        change_24h_pct=change_24h_pct,
        short_window_change_pct=short_window_change_pct,
        volume_24h=volume_24h,
        priced_in=priced_in,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# price-in 判定
# ---------------------------------------------------------------------------


def _judge_priced_in(direction: str, short_window_change_pct: float) -> tuple[bool, str]:
    threshold_pct = config.PRICE_IN_THRESHOLD_PCT * 100  # config 存的是比例(0.03),这里换算成百分比数值口径

    normalized_direction = (direction or "").strip().lower()
    if normalized_direction == "bullish":
        if short_window_change_pct >= threshold_pct:
            return True, (
                f"方向 bullish,短窗口({SHORT_WINDOW_MINUTES}分钟)涨幅 {short_window_change_pct:.2f}% "
                f"≥ 阈值 {threshold_pct:.2f}%,判定已被市场消化"
            )
        return False, (
            f"方向 bullish,短窗口涨幅 {short_window_change_pct:.2f}% 未达阈值 {threshold_pct:.2f}%,未消化"
        )
    if normalized_direction == "bearish":
        if short_window_change_pct <= -threshold_pct:
            return True, (
                f"方向 bearish,短窗口跌幅 {short_window_change_pct:.2f}% "
                f"≥ 阈值 {threshold_pct:.2f}%,判定已被市场消化"
            )
        return False, (
            f"方向 bearish,短窗口跌幅 {short_window_change_pct:.2f}% 未达阈值 {threshold_pct:.2f}%,未消化"
        )

    # 方向不是 bullish/bearish(理论上 irrelevant 应在 extractor 就短路,不会走到这一层):
    # 无法判断价格变动是否符合信号方向,保守 fail-closed。
    logger.warning("market 未知信号方向 %r,fail-closed 否决", direction)
    return True, f"未知信号方向 {direction!r},无法判定 price-in,fail-closed 否决"


def _snapshot(
    *,
    asset: str,
    symbol: str | None,
    direction: str,
    market_available: bool,
    current_price: float | None,
    change_24h_pct: float | None,
    short_window_change_pct: float | None,
    volume_24h: float | None,
    priced_in: bool,
    reason: str,
) -> dict:
    return {
        "asset": asset,
        "symbol": symbol,
        "direction": direction,
        "marketAvailable": market_available,
        "currentPrice": current_price,
        "change24hPct": change_24h_pct,
        "shortWindowChangePct": short_window_change_pct,
        "shortWindowMinutes": SHORT_WINDOW_MINUTES,
        "volume24h": volume_24h,
        "pricedIn": priced_in,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# 币安接口调用
# ---------------------------------------------------------------------------


def _fetch_ticker_24hr(symbol: str) -> dict:
    """GET /api/v3/ticker/24hr:当前价、24h 涨跌幅、24h 成交量。"""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/24hr?{urllib.parse.urlencode({'symbol': symbol})}"
    body = _http_get_with_retry(url)
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MarketError(f"ticker/24hr 响应不是合法 JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MarketError(f"ticker/24hr 响应结构异常,预期对象,得到 {type(data).__name__}")

    try:
        return {
            "current_price": float(data["lastPrice"]),
            "change_24h_pct": float(data["priceChangePercent"]),
            "volume_24h": float(data["volume"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketError(f"ticker/24hr 响应字段缺失或类型异常: {exc}") from exc


def _fetch_short_window_change_pct(symbol: str) -> float:
    """GET /api/v3/klines(1m):用窗口首根开盘价 vs 最新收盘价算短窗口涨跌幅(百分比数值)。"""
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": "1m",
            "limit": str(SHORT_WINDOW_MINUTES),
        }
    )
    url = f"{BINANCE_BASE_URL}/api/v3/klines?{params}"
    body = _http_get_with_retry(url)
    try:
        klines = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MarketError(f"klines 响应不是合法 JSON: {exc}") from exc

    if not isinstance(klines, list) or not klines:
        raise MarketError(f"klines 响应结构异常或为空: {type(klines).__name__}")

    try:
        # 币安 kline 数组下标:1=open, 4=close(详见币安公开文档字段顺序)
        window_open = float(klines[0][1])
        latest_close = float(klines[-1][4])
    except (IndexError, TypeError, ValueError) as exc:
        raise MarketError(f"klines 响应字段缺失或类型异常: {exc}") from exc

    if window_open == 0:
        raise MarketError("klines 窗口首根开盘价为 0,无法计算涨跌幅")

    return (latest_close - window_open) / window_open * 100


# ---------------------------------------------------------------------------
# HTTP 工具(标准库 urllib,超时 + 指数退避重试;与 collector.py 同一套模式)
# ---------------------------------------------------------------------------


def _http_get_with_retry(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS, max_retries: int = DEFAULT_MAX_RETRIES) -> str:
    """GET 并返回响应文本;网络错误/5xx/429 指数退避重试,其余 4xx 不重试。"""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.info(
                "market 第 %d 次重试,退避 %.1fs url_host=%s", attempt, delay, urllib.parse.urlparse(url).netloc
            )
            time.sleep(delay)
        try:
            return _http_get(url, timeout)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 or exc.code >= 500:
                logger.warning("market HTTP %d,准备重试", exc.code)
                continue
            raise MarketError(f"HTTP {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            logger.warning("market 网络错误: %s,准备重试", exc)
            continue
    raise MarketError(f"重试 {max_retries} 次后仍失败: {last_error}") from last_error


def _http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "trump-signal-trader/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")
