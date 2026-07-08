# -*- coding: utf-8 -*-
"""notifier.py —— Telegram Bot 消息模板 + 发送(重试)。

标准库 urllib 调用 https://api.telegram.org/bot{token}/sendMessage,
token/chat_id 从 signal/config.py 读取,不在本模块散落 os.environ.get(...)〔coding-style.md〕。

三类消息模板(见 design.md 接口契约节):
1) 信号消息 send_signal_message  —— 帖子摘要+链接、资产、方向、双模型置信度、行情快照、核查结论
2) 告警消息 send_alert_message   —— 源切换/异常摘要
3) 否决消息 send_rejection_message —— 否决层 + 原因,由 config.NOTIFY_ON_REJECTION 开关控制,默认关

安全:
- parse_mode 用 HTML,帖子原文等用户内容一律经 html.escape 转义后再拼进消息体〔security.md 不可信输入〕;
- 消息体截断到 Telegram 4096 字符限制内;
- 发送失败指数退避重试,重试耗尽抛 NotifierError;
- 任何日志/异常信息都不得包含 token(URL 中的 token 分段禁止打印,仅打印脱敏 host/path)〔security.md〕。
"""

from __future__ import annotations

import html
import json
import logging
import random
import time
import urllib.error
import urllib.request
from typing import Any

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TELEGRAM_API_BASE = "https://api.telegram.org"
# Telegram sendMessage 文本上限;贴近上限截断,留余量给截断提示文案
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_TRUNCATE_SUFFIX = "\n…(内容过长已截断)"

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0


class NotifierError(Exception):
    """Telegram 发送重试耗尽后仍失败;消息文本里绝不含 token。"""


# ---------------------------------------------------------------------------
# 三类消息模板(对外接口)
# ---------------------------------------------------------------------------


def send_signal_message(
    *,
    post_summary: str,
    post_url: str,
    assets: list[str],
    direction: str,
    extractor_confidence: float,
    validator_confidence: float,
    market_snapshot: str,
    factcheck_verdict: str,
) -> None:
    """信号消息:帖子摘要+链接、资产、方向、双模型置信度、行情快照、核查结论。"""
    lines = [
        "<b>🚨 交易信号</b>",
        "",
        f"<b>资产</b>: {_esc(', '.join(assets) or '未知')}",
        f"<b>方向</b>: {_esc(direction)}",
        f"<b>置信度</b>: 提取模型 {extractor_confidence:.2f} / 验证模型 {validator_confidence:.2f}",
        f"<b>行情快照</b>: {_esc(market_snapshot)}",
        f"<b>核查结论</b>: {_esc(factcheck_verdict)}",
        "",
        f"<b>帖子摘要</b>: {_esc(post_summary)}",
    ]
    link = _link_line(post_url)
    if link:
        lines.append(link)
    _send(_join(lines))


def send_alert_message(*, summary: str, detail: str = "") -> None:
    """告警消息:采集源切换/异常堆栈摘要。detail 为可能不可信/敏感的原始信息,一并转义。"""
    lines = [
        "<b>⚠️ 系统告警</b>",
        "",
        _esc(summary),
    ]
    if detail:
        lines += ["", f"<pre>{_esc(detail)}</pre>"]
    _send(_join(lines))


def send_rejection_message(*, step: str, reason: str, post_url: str = "") -> None:
    """否决消息:否决层 + 原因。由 config.NOTIFY_ON_REJECTION 开关控制是否真正发送,默认关。"""
    if not config.NOTIFY_ON_REJECTION:
        logger.info("notifier 否决消息已生成但开关关闭,不发送 step=%s", step)
        return
    lines = [
        "<b>🛑 信号被否决</b>",
        "",
        f"<b>否决层</b>: {_esc(step)}",
        f"<b>原因</b>: {_esc(reason)}",
    ]
    link = _link_line(post_url)
    if link:
        lines.append(link)
    _send(_join(lines))


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    """用户/外部内容(帖子原文等)按不可信输入处理:HTML 转义后才能拼进 parse_mode=HTML 消息体。

    quote=True 必须保留:转义结果会被拼进 href="..." 双引号属性,不转义引号
    会让不可信的 post_url 逃逸属性上下文注入任意 HTML(review P0)。
    """
    return html.escape(str(value), quote=True)


def _link_line(url: str) -> str | None:
    """生成「查看原帖」链接行;URL 不可信,仅放行 http/https,否则不输出链接。"""
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return f'<a href="{_esc(url)}">查看原帖</a>'
    return None


def _join(lines: list[str]) -> str:
    text = "\n".join(lines)
    if len(text) > TELEGRAM_MAX_MESSAGE_LENGTH:
        text = text[: TELEGRAM_MAX_MESSAGE_LENGTH - len(_TRUNCATE_SUFFIX)] + _TRUNCATE_SUFFIX
    return text


def _send(text: str) -> None:
    """发送到 config 配置的 chat_id,指数退避重试;最终失败抛 NotifierError(不泄漏 token)。"""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        # 未配置第三方凭证时降级为仅记录日志,不阻塞 pipeline(TODO: 部署时通过 SAM NoEcho 参数注入)
        logger.warning("notifier TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 未配置,跳过发送")
        return

    url = f"{TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
    ).encode("utf-8")

    last_error: Exception | None = None
    for attempt in range(DEFAULT_MAX_RETRIES):
        if attempt > 0:
            delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.info("notifier 第 %d 次重试,退避 %.1fs", attempt, delay)
            time.sleep(delay)
        try:
            _post(url, payload)
            logger.info("notifier 发送成功 attempt=%d", attempt + 1)
            return
        except urllib.error.HTTPError as exc:
            # 响应体可能回显请求内容,不打印;只记录状态码
            last_error = exc
            if exc.code == 429 or exc.code >= 500:
                logger.warning("notifier HTTP %d,准备重试", exc.code)
                continue
            # 4xx(除限流外)重试无意义,例如 400 请求格式错误、401/403 凭证问题
            logger.error("notifier HTTP %d,不再重试", exc.code)
            raise NotifierError(f"Telegram 发送失败: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            logger.warning("notifier 网络错误,准备重试: %s", type(exc).__name__)
            continue

    # 异常信息只带类型名,不带 last_error 的字符串化内容(可能内嵌请求 URL/token)
    raise NotifierError(
        f"Telegram 发送重试 {DEFAULT_MAX_RETRIES} 次后仍失败: {type(last_error).__name__ if last_error else '未知错误'}"
    ) from None


def _post(url: str, payload: bytes) -> None:
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as resp:
        resp.read()
