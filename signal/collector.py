# -*- coding: utf-8 -*-
"""采集器:抓取 @realDonaldTrump 的 Truth Social 新帖,统一返回 list[Post]。

T-003 实现了主方案(未认证 statuses API 轮询);T-004(本次改动)补齐:
- 降级方案 B:RSS 镜像(trumpstruth.org)解析,标准库 xml.etree,产出同构 Post;
- 主/降级源自动切换:源状态(activeSource/failCount/lastOkTs)落 DynamoDB(store.py),
  主源连续失败达 config.SOURCE_FAIL_THRESHOLD 次后切到降级源;
  切到降级源期间,每轮先探测一次主源,探测成功即视为恢复并切回。

实测发现(T-003 验证阶段):本机对 truthsocial.com statuses API 的请求被 Cloudflare
返回 403(质询页,非 JSON)。design.md 风险预案允许把 RSS 镜像升级为主源,但本次决定
**维持 design.md 已选定的方案:方案 A 为主源、方案 B 为降级源**,理由:
1) 接口契约本就是 primary/fallback 抽象,「升级为主源」只是切默认 activeSource,
   不改变故障处理路径,不必现在锁死方案取舍,后续观测数据支持随时可调;
2) 403 可能是本机 IP/网络环境或 Cloudflare 当时策略导致,Lambda 出口 IP 与本机不同,
   不能仅凭一次本地实测排除方案 A 长期可用性;
3) 已实现「一轮内快速降级」:阈值默认 3(config.SOURCE_FAIL_THRESHOLD),配合 EventBridge
   每分钟触发,最坏 3 分钟内完成切换,降级期间不丢新帖(本轮失败达阈值即用降级源续拉);
4) 若线上观测到主源持续 403,只需调低 SOURCE_FAIL_THRESHOLD 或把源状态初始值改为
   fallback(store.get_source_state 默认值),无需改代码。

设计要点(见 specs/3.trump-signal-trader/design.md):
- Truth Social 基于 Mastodon,`GET /api/v1/accounts/{id}/statuses` 部分场景免登录可读;
- 带浏览器 UA 降低被 Cloudflare 拦截概率;所有请求设超时;失败指数退避重试;
- 外部 JSON/XML 按不可信输入处理:字段缺失/类型异常跳过该条,不崩;
- 只用标准库(Lambda 运行时无 requests)。
"""

from __future__ import annotations

import datetime as dt
import email.utils
import gzip
import html
import json
import logging
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import config
import store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量(可调参数一律从 config.py 读,不在本模块散落 os.environ.get〔coding-style.md〕)
# ---------------------------------------------------------------------------

# @realDonaldTrump 在 Truth Social(Mastodon 体系)上的账号数值 ID,公开可查、恒定不变
TRUMP_ACCOUNT_ID = "107780257626128497"

# 伪装成常见桌面浏览器,Cloudflare 对无 UA/脚本 UA 的请求拦截更激进
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 去掉帖子正文里的 HTML 标签(Mastodon content 字段是 HTML)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Post:
    """采集到的一条帖子,主/降级源统一产出该结构。"""

    post_id: str  # 平台帖子 ID,去重主键
    content: str  # 纯文本正文(已去 HTML 标签、反转义)
    created_at: str  # ISO8601 时间戳(平台原始值)
    url: str  # 帖子永久链接
    source: str = "primary"  # 来源标识:primary / fallback,便于链路回查
    raw: dict = field(default_factory=dict, compare=False)  # 原始条目,落库备查


class CollectorError(Exception):
    """单个采集源在重试耗尽后仍失败。

    T-004 的切换逻辑依赖该异常:主源抛 CollectorError → 累计失败次数 → 达阈值切降级源。
    """


# ---------------------------------------------------------------------------
# 源抽象(给 T-004 降级方案留的扩展点)
# ---------------------------------------------------------------------------


class Source:
    """采集源统一接口:fetch_latest() -> list[Post],失败抛 CollectorError。"""

    name = "abstract"

    def fetch_latest(self) -> list[Post]:
        raise NotImplementedError


class TruthSocialApiSource(Source):
    """主方案:Truth Social 未认证 statuses API 轮询。"""

    name = "primary"

    def __init__(
        self,
        account_id: str = TRUMP_ACCOUNT_ID,
        base_url: str = config.TRUTH_SOCIAL_BASE_URL,
        limit: int = config.COLLECTOR_FETCH_LIMIT,
        timeout: float = config.COLLECTOR_TIMEOUT_SECONDS,
        max_retries: int = config.COLLECTOR_MAX_RETRIES,
    ):
        self.account_id = account_id
        self.base_url = base_url.rstrip("/")
        self.limit = limit
        self.timeout = timeout
        self.max_retries = max_retries

    # -- 对外接口 -----------------------------------------------------------

    def fetch_latest(self) -> list[Post]:
        """拉取最新帖子列表(按发布时间倒序),重试耗尽抛 CollectorError。"""
        params = urllib.parse.urlencode(
            {
                # 只要本人原创发言:回复/转发都排除。转发(reblog)的 content 字段为空,
                # 会产出空文本 Post 流入 pipeline;转发的原帖本身也另有账号可去重,排除更干净
                "exclude_replies": "true",
                "exclude_reblogs": "true",
                "limit": str(self.limit),
            }
        )
        url = f"{self.base_url}/api/v1/accounts/{self.account_id}/statuses?{params}"
        body = _http_get_with_retry(url, timeout=self.timeout, max_retries=self.max_retries)

        try:
            items = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # 返回了非 JSON(典型:Cloudflare 质询页),视为源失败交给上层计数
            raise CollectorError(f"{self.name}: 响应不是合法 JSON: {exc}") from exc

        if not isinstance(items, list):
            raise CollectorError(f"{self.name}: 响应结构异常,预期数组,得到 {type(items).__name__}")

        posts = []
        for item in items:
            post = self._parse_item(item)
            if post is not None:
                posts.append(post)
        logger.info("collector source=%s fetched=%d parsed=%d", self.name, len(items), len(posts))
        return posts

    # -- 内部实现 -----------------------------------------------------------

    def _parse_item(self, item) -> Post | None:
        """单条 status → Post;字段缺失/类型异常跳过该条(不可信输入不崩)。"""
        if not isinstance(item, dict):
            return None
        post_id = item.get("id")
        created_at = item.get("created_at")
        if not isinstance(post_id, str) or not post_id or not isinstance(created_at, str):
            logger.warning("collector source=%s 跳过缺关键字段的条目", self.name)
            return None

        content = item.get("content")
        text = _strip_html(content) if isinstance(content, str) else ""
        if not text:
            # 转发(reblog)等 content 为空的条目对信号提取无意义,即使 exclude_reblogs
            # 未生效也在此兜底跳过,不让空文本流入 pipeline
            logger.info("collector source=%s 跳过空文本条目 post_id=%s", self.name, post_id)
            return None

        url = item.get("url") or item.get("uri")
        if not isinstance(url, str) or not url:
            url = f"{self.base_url}/@realDonaldTrump/posts/{post_id}"

        return Post(post_id=post_id, content=text, created_at=created_at, url=url, source=self.name, raw=item)


class RssMirrorSource(Source):
    """降级方案:第三方 RSS 镜像站解析(design.md 方案 B)。

    镜像延迟以分钟计但更稳定、无 Cloudflare 拦截问题,主源连续失败时接管采集。
    """

    name = "fallback"

    def __init__(
        self,
        feed_url: str = config.RSS_MIRROR_URL,
        timeout: float = config.COLLECTOR_TIMEOUT_SECONDS,
        max_retries: int = config.COLLECTOR_MAX_RETRIES,
    ):
        self.feed_url = feed_url
        self.timeout = timeout
        self.max_retries = max_retries

    # -- 对外接口 -----------------------------------------------------------

    def fetch_latest(self) -> list[Post]:
        """拉取并解析 RSS feed,重试耗尽或 XML 非法抛 CollectorError。"""
        body = _http_get_with_retry(self.feed_url, timeout=self.timeout, max_retries=self.max_retries)

        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise CollectorError(f"{self.name}: 响应不是合法 XML: {exc}") from exc

        items = root.findall("./channel/item")
        posts = []
        for item in items:
            post = self._parse_item(item)
            if post is not None:
                posts.append(post)
        logger.info("collector source=%s fetched=%d parsed=%d", self.name, len(items), len(posts))
        return posts

    # -- 内部实现 -----------------------------------------------------------

    def _parse_item(self, item: ET.Element) -> Post | None:
        """单条 RSS <item> → Post;字段缺失/类型异常跳过该条(不可信输入不崩)。"""
        link = _rss_child_text(item, "link")
        guid = _rss_child_text(item, "guid") or link
        pub_date = _rss_child_text(item, "pubDate")
        description = _rss_child_text(item, "description") or ""

        if not guid or not link:
            logger.warning("collector source=%s 跳过缺 guid/link 的条目", self.name)
            return None

        post_id = _rss_post_id(guid)
        created_at = _rss_pub_date_to_iso(pub_date) if pub_date else ""
        text = _strip_html(html.unescape(description)) if description else ""
        if not text:
            # 与主源同一策略:空文本条目对信号提取无意义,直接跳过
            logger.info("collector source=%s 跳过空文本条目 post_id=%s", self.name, post_id)
            return None

        return Post(
            post_id=post_id,
            content=text,
            created_at=created_at,
            url=link,
            source=self.name,
            raw={child.tag: child.text for child in item},
        )


# ---------------------------------------------------------------------------
# 模块级入口(pipeline 调用点):按源状态选主/降级源 + 失败自动切换 + 恢复切回
# ---------------------------------------------------------------------------


def fetch_latest() -> list[Post]:
    """采集入口:读源状态(store.get_source_state)决定走主源还是降级源。

    - 当前主源(primary):正常拉取;成功则清零失败计数并记录 lastOkTs;
      失败则失败计数 +1,达到 config.SOURCE_FAIL_THRESHOLD 时切到降级源
      并在本轮立即用降级源续拉(不空转、不丢这一分钟的新帖);未达阈值时
      本轮采集失败,异常上抛由 app.py 编排层告警(design.md「系统异常...发 Telegram 告警」)。
    - 当前降级源(fallback):每轮先探测一次主源,探测成功即视为主源恢复,
      切回 primary 并直接使用探测到的结果;探测失败则维持降级源,正常用
      降级源拉取(不因主源探测失败而累加失败计数——降级源本身是否可用才是本轮结果)。
    """
    state = store.get_source_state()
    active = state.get("activeSource", "primary")
    fail_count = int(state.get("failCount", 0) or 0)

    if active == "fallback":
        return _fetch_from_fallback_with_recovery_probe()
    return _fetch_from_primary(fail_count)


def _fetch_from_primary(fail_count: int) -> list[Post]:
    try:
        posts = TruthSocialApiSource().fetch_latest()
    except CollectorError as exc:
        fail_count += 1
        logger.warning("collector 主源失败 第%d次(阈值%d): %s", fail_count, config.SOURCE_FAIL_THRESHOLD, exc)
        if fail_count >= config.SOURCE_FAIL_THRESHOLD:
            store.put_source_state(active_source="fallback", fail_count=0, last_ok_ts=None)
            logger.warning("collector 主源连续失败达阈值,切换到降级源 fallback")
            _alert_switch(f"主源连续失败 {fail_count} 次,已切换到降级源(RSS 镜像)", str(exc))
            # 本轮立即用降级源续拉,避免这一分钟采集空转
            return RssMirrorSource().fetch_latest()
        store.put_source_state(active_source="primary", fail_count=fail_count, last_ok_ts=None)
        raise
    else:
        store.put_source_state(active_source="primary", fail_count=0, last_ok_ts=_now_iso())
        return posts


def _fetch_from_fallback_with_recovery_probe() -> list[Post]:
    try:
        posts = TruthSocialApiSource().fetch_latest()
    except CollectorError as exc:
        logger.info("collector 主源恢复探测仍失败,继续用降级源: %s", exc)
    else:
        store.put_source_state(active_source="primary", fail_count=0, last_ok_ts=_now_iso())
        logger.info("collector 主源恢复探测成功,已切回 primary")
        _alert_switch("主源恢复,已从降级源切回 primary", "")
        return posts

    posts = RssMirrorSource().fetch_latest()
    store.put_source_state(active_source="fallback", fail_count=0, last_ok_ts=None)
    return posts


def _alert_switch(summary: str, detail: str) -> None:
    """源切换/恢复时发 Telegram 告警(AC-009)。

    告警失败绝不能反过来打断采集(告警是旁路),吞掉异常只记日志;
    延迟 import 避免 collector→notifier 的模块级循环依赖风险。
    """
    try:
        import notifier

        notifier.send_alert_message(summary=f"采集源切换: {summary}", detail=detail)
    except Exception as exc:  # noqa: BLE001 - 旁路告警失败只记日志
        logger.error("collector 源切换告警发送失败: %s", type(exc).__name__)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# HTTP 工具(标准库 urllib,超时 + 指数退避重试)
# ---------------------------------------------------------------------------


def _http_get_with_retry(url: str, timeout: float, max_retries: int) -> str:
    """GET 并返回响应文本;网络错误/5xx/429 指数退避重试,4xx(限流除外)不重试。"""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            # 指数退避 + 随机抖动,避免固定节奏撞限流
            delay = config.COLLECTOR_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.info("collector 第 %d 次重试,退避 %.1fs url_host=%s", attempt, delay, urllib.parse.urlparse(url).netloc)
            time.sleep(delay)
        try:
            return _http_get(url, timeout)
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 429/5xx 是暂时性故障值得重试;其余 4xx(403 拦截、404 等)重试无意义
            if exc.code == 429 or exc.code >= 500:
                logger.warning("collector HTTP %d,准备重试", exc.code)
                continue
            raise CollectorError(f"HTTP {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            logger.warning("collector 网络错误: %s,准备重试", exc)
            continue
    raise CollectorError(f"重试 {max_retries} 次后仍失败: {last_error}") from last_error


def _http_get(url: str, timeout: float) -> str:
    """GET 单次请求;响应体大小设上限(config.COLLECTOR_MAX_RESPONSE_BYTES),
    超限视为源异常按 CollectorError 处理,避免异常/恶意大响应把 Lambda 内存占爆。
    """
    max_bytes = config.COLLECTOR_MAX_RESPONSE_BYTES
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # 多读 1 字节即可判断是否超限,不必把整个超限响应体都读进内存
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise CollectorError(f"响应体超过 {max_bytes} 字节上限: {url}")
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data.decode("utf-8")


def _strip_html(content: str) -> str:
    """Mastodon content / RSS description 都是 HTML:段落转换行、去标签、反转义实体。"""
    text = re.sub(r"</p>\s*<p>", "\n\n", content)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = _HTML_TAG_RE.sub("", text)
    return html.unescape(text).strip()


# ---------------------------------------------------------------------------
# RSS 解析工具(降级源专用)
# ---------------------------------------------------------------------------


def _rss_child_text(item: ET.Element, tag: str) -> str | None:
    """取 <item> 下某子标签的文本,标签不存在/为空返回 None(不可信输入不崩)。"""
    child = item.find(tag)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _rss_post_id(guid: str) -> str:
    """guid 常是完整 URL(如 https://trumpstruth.org/statuses/12345),取末段做 post_id;
    与主源的纯数字 ID 空间不同,统一加 fallback 前缀避免误撞主源 ID 造成假去重。"""
    tail = guid.rstrip("/").rsplit("/", 1)[-1]
    return f"rss-{tail}" if tail else f"rss-{guid}"


def _rss_pub_date_to_iso(pub_date: str) -> str:
    """RSS pubDate 是 RFC 822 格式,统一转成 ISO8601 与主源对齐;解析失败原样返回。"""
    try:
        parsed = email.utils.parsedate_to_datetime(pub_date)
    except (TypeError, ValueError):
        return pub_date
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat()
