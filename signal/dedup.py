# -*- coding: utf-8 -*-
"""dedup.py —— 帖子 ID 幂等 + 内容规范化哈希 + 30 天旧闻窗口检测。

只做业务逻辑,不摸 boto3:ID 幂等与哈希占坑全部委托 store.py 的条件写函数
(put_post / claim_content_hash),这里负责「内容规范化 → sha256 哈希」的
纯函数计算,以及把 store 抛出的两类竞态异常翻译成明确的判定结果供
pipeline(app.py, T-011)据此短路。

判定结果三态(design.md pipeline:「dedup(ID去重 + 内容哈希/旧闻) ──否决→ 落库 DUPLICATE」):
- NEW:            首次出现的帖子 ID 且内容在旧闻窗口内未出现过 → 放行进入下一层
- DUPLICATE_ID:   帖子 ID 已存在(同一帖子被重复采集/重放) → 否决
- STALE_CONTENT:  帖子 ID 是新的,但规范化后的内容哈希在 STALE_NEWS_WINDOW_DAYS
                  天内已登记过(转发/文案微调的旧闻) → 否决
"""

from __future__ import annotations

import hashlib
import re
import string
from dataclasses import dataclass

import store

# 规范化时要剥离的 URL(帖子正文常见的链接,不同转发可能带不同短链/追踪参数,
# 若不剥离会导致同一内容因链接不同而哈希不同,漏判旧闻)
_URL_RE = re.compile(r"https?://\S+")

# 规范化时要折叠的连续空白(空格/换行/制表符等)
_WHITESPACE_RE = re.compile(r"\s+")

# 标点差异(全角/半角、感叹号数量等)在规范化阶段一律剔除,只保留文字与数字信息
_PUNCTUATION_TABLE = str.maketrans("", "", string.punctuation + "！？，。、；：""''（）《》…—")


@dataclass(frozen=True)
class DedupResult:
    """dedup 判定结果,供 app.py 编排层落库决策记录 + 短路。"""

    verdict: str  # NEW / DUPLICATE_ID / STALE_CONTENT
    content_hash: str
    detail: dict


def normalize_content(content: str) -> str:
    """内容规范化:去 URL → 去标点 → 大小写折叠 → 空白折叠。

    目标是让「同一条消息的不同转发/轻微文案调整」规范化后落到同一个哈希,
    从而被 30 天旧闻窗口捕获;规范化本身不落库、不外呼,是纯函数便于单测。
    """
    text = content or ""
    text = _URL_RE.sub("", text)
    text = text.translate(_PUNCTUATION_TABLE)
    text = text.lower()
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def compute_content_hash(content: str) -> str:
    """规范化内容 → sha256 十六进制摘要,作为 HASH#{contentHash} 索引的分区键。"""
    normalized = normalize_content(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def check(post_id: str, content: str, created_at: str) -> DedupResult:
    """dedup 层入口:ID 幂等 + 旧闻窗口检测,均通过则视为 NEW 并完成两项登记。

    顺序:先登记帖子 META(ID 幂等条件写),ID 已存在直接短路为 DUPLICATE_ID,
    不再计算/占用哈希索引;ID 通过后再登记内容哈希(旧闻窗口条件写),
    命中则短路为 STALE_CONTENT。两步都用 store 的条件写而非先读后写,
    与 database.md「幂等/去重用条件写」的约定一致,避免并发重放的竞态。
    """
    content_hash = compute_content_hash(content)

    try:
        store.put_post(post_id=post_id, content=content, created_at=created_at, content_hash=content_hash)
    except store.PostAlreadyExists:
        return DedupResult(
            verdict="DUPLICATE_ID",
            content_hash=content_hash,
            detail={"reason": "post_id 已存在,判定为重复采集/重放", "postId": post_id},
        )

    try:
        store.claim_content_hash(content_hash=content_hash, post_id=post_id)
    except store.StaleContent:
        return DedupResult(
            verdict="STALE_CONTENT",
            content_hash=content_hash,
            detail={
                "reason": "规范化内容哈希在旧闻窗口内已登记过,判定为旧闻/文案微调转发",
                "postId": post_id,
                "contentHash": content_hash,
            },
        )

    return DedupResult(
        verdict="NEW",
        content_hash=content_hash,
        detail={"postId": post_id, "contentHash": content_hash},
    )
