# -*- coding: utf-8 -*-
"""store.py —— DynamoDB 单表读写封装(SignalTable)。

沿用 auth/store.py 的风格:把所有 boto3 访问收敛在这一个文件里,
pipeline 各模块(collector/dedup/extractor/...)只调用这里的函数,不直接摸 boto3。

单表设计(见 specs/3.trump-signal-trader/design.md「数据模型」,pk/sk 已定死):

| 实体         | pk                     | sk                     | 关键属性 |
|--------------|------------------------|------------------------|----------|
| 帖子         | POST#{post_id}         | META                   | content, createdAt, status, contentHash, ttl(90天) |
| 决策步骤     | POST#{post_id}         | STEP#{seq}#{step}      | step, result, detail(JSON字符串), ts |
| 内容哈希索引 | HASH#{contentHash}     | META                   | postId, ts, ttl(30天) |
| 采集源状态   | SOURCE#state           | META                   | activeSource, failCount, lastOkTs |

去重与旧闻检测统一用条件写(attribute_not_exists),不先读后写,避免竞态〔database.md〕。
"""

import datetime as dt
import json
import time

import boto3
from botocore.exceptions import ClientError

import config

# boto3 资源句柄放在模块级别,Lambda 容器复用,避免每次调用重新建连接
_dynamodb = boto3.resource("dynamodb")

# sk 前缀常量,避免魔法字符串散落各处
_SK_META = "META"
_SK_STEP_PREFIX = "STEP#"
_PK_SOURCE_STATE = "SOURCE#state"


def _table():
    """延迟获取表对象(方便测试时用 moto mock 替换连接)。"""
    return _dynamodb.Table(config.SIGNAL_TABLE_NAME)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ttl_after_days(days: int) -> int:
    """DynamoDB TTL 属性要求 epoch 秒(Number),到期后自动清理该条目。"""
    return int(time.time()) + days * 86400


class PostAlreadyExists(Exception):
    """帖子 META 已存在(同一 post_id 重复采集),上层据此判定为幂等短路。"""


class StaleContent(Exception):
    """内容哈希在旧闻窗口内已出现过,上层据此否决为 DUPLICATE/旧闻。"""


# ---------------------------------------------------------------------------
# 帖子 META
# ---------------------------------------------------------------------------


def put_post(post_id: str, content: str, created_at: str, content_hash: str, status: str = "FETCHED") -> dict:
    """写入帖子 META,条件写保证幂等(同一 post_id 只成功一次)。

    幂等/去重用条件写而非先读后写〔database.md〕:即使 pipeline 因重试对同一帖子
    执行两次,也只有第一次真正落库,第二次抛 PostAlreadyExists 由上层短路处理。
    """
    item = {
        "pk": f"POST#{post_id}",
        "sk": _SK_META,
        "postId": post_id,
        "content": content,
        "createdAt": created_at,
        "contentHash": content_hash,
        "status": status,
        "ttl": _ttl_after_days(config.POST_TTL_DAYS),
    }
    try:
        _table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise PostAlreadyExists(post_id)
        raise
    return item


def update_post_status(post_id: str, status: str) -> None:
    """更新帖子当前所处 pipeline 状态(FETCHED/…/SIGNAL_SENT 等)。"""
    _table().update_item(
        Key={"pk": f"POST#{post_id}", "sk": _SK_META},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status},
        ConditionExpression="attribute_exists(pk)",
    )


def get_post(post_id: str) -> dict | None:
    """按 post_id 读取帖子 META,找不到返回 None。"""
    resp = _table().get_item(Key={"pk": f"POST#{post_id}", "sk": _SK_META})
    return resp.get("Item")


# ---------------------------------------------------------------------------
# 决策步骤(STEP#{seq}#{step} 追加)
# ---------------------------------------------------------------------------


def append_step(post_id: str, seq: int, step: str, result: str, detail: dict | None = None) -> dict:
    """追加一条决策记录。

    sk 用 `STEP#{seq:04d}#{step}` 零填充保证按 pipeline 执行顺序字典序排列;
    seq 由调用方(app.py 编排层)按 pipeline 阶段序号传入(1=dedup, 2=extract, ...)。
    detail 是任意结构化附加信息(如双模型分歧详情、行情快照),序列化成 JSON 字符串存储,
    避免 DynamoDB 对嵌套 float 类型的 Decimal 限制。
    """
    item = {
        "pk": f"POST#{post_id}",
        "sk": f"{_SK_STEP_PREFIX}{seq:04d}#{step}",
        "step": step,
        "result": result,
        "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
        "ts": _now_iso(),
    }
    _table().put_item(Item=item)
    return item


def get_chain(post_id: str) -> dict:
    """回查某帖子的完整链路(AC-010):一次 query 取回 META + 全部 STEP 记录。

    返回 {"post": <META或None>, "steps": [<STEP...>按sk升序即执行顺序]}。
    """
    resp = _table().query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": f"POST#{post_id}"},
    )
    items = resp.get("Items", [])
    post = None
    steps = []
    for item in items:
        if item["sk"] == _SK_META:
            post = item
        elif item["sk"].startswith(_SK_STEP_PREFIX):
            steps.append(item)
    steps.sort(key=lambda i: i["sk"])
    return {"post": post, "steps": steps}


# ---------------------------------------------------------------------------
# 内容哈希索引(旧闻检测,条件写)
# ---------------------------------------------------------------------------


def claim_content_hash(content_hash: str, post_id: str) -> dict:
    """尝试登记内容哈希;若窗口内已存在同一哈希,判定为旧闻并抛 StaleContent。

    条件写而非先读后写:两个并发请求撞同一内容哈希时,只有一个能登记成功,
    与帖子去重同一模式〔database.md〕。TTL 到期后旧条目自动清理,窗口外的
    相同内容会被视为"新"——这正是「30 天旧闻窗口」的语义。
    """
    item = {
        "pk": f"HASH#{content_hash}",
        "sk": _SK_META,
        "postId": post_id,
        "ts": _now_iso(),
        "ttl": _ttl_after_days(config.HASH_TTL_DAYS),
    }
    try:
        _table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise StaleContent(content_hash)
        raise
    return item


def get_content_hash(content_hash: str) -> dict | None:
    """读取内容哈希索引条目(调试/回查用,dedup.py 判定优先走 claim_content_hash)。"""
    resp = _table().get_item(Key={"pk": f"HASH#{content_hash}", "sk": _SK_META})
    return resp.get("Item")


# ---------------------------------------------------------------------------
# 采集源状态(SOURCE#state)
# ---------------------------------------------------------------------------


def get_source_state() -> dict:
    """读取采集源状态;首次运行(尚未写入过)返回默认值(主源、失败计数 0)。"""
    resp = _table().get_item(Key={"pk": _PK_SOURCE_STATE, "sk": _SK_META})
    item = resp.get("Item")
    if item is None:
        return {"activeSource": "primary", "failCount": 0, "lastOkTs": None}
    return item


def put_source_state(active_source: str, fail_count: int, last_ok_ts: str | None = None) -> dict:
    """覆盖写入采集源状态。单行状态记录,读多写少,直接整体覆盖即可,无需条件写。"""
    item = {
        "pk": _PK_SOURCE_STATE,
        "sk": _SK_META,
        "activeSource": active_source,
        "failCount": fail_count,
        "lastOkTs": last_ok_ts,
    }
    _table().put_item(Item=item)
    return item
