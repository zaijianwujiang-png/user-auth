# -*- coding: utf-8 -*-
"""app.py —— Lambda 入口 + pipeline 编排(T-011)。

EventBridge 每分钟触发本函数(design.md「方案概述」):

    collector(主/降级源) → 新帖?
        dedup(ID去重 + 内容哈希/旧闻)      ──否决→ DUPLICATE
        extractor(claude-fable-5 提取信号)  ──无关→ IRRELEVANT
        validator(claude-sonnet-5 独立判断) ──分歧→ MODEL_DISAGREE
        market(币安公开行情 price-in 检查)  ──已消化→ PRICED_IN
        factcheck(Claude web search 核实)   ──存疑→ FACT_CHECK_FAILED
        notifier(Telegram 推送)             → SIGNAL_SENT

编排原则:
- 每一步都用 store.append_step 落一条决策记录(seq 按 pipeline 顺序递增:
  1=dedup 2=extract 3=validate 4=market 5=factcheck 6=notify),并同步
  store.update_post_status 更新帖子当前状态,任一层否决即短路进入下一个帖子;
- 否决消息经 notifier.send_rejection_message 发送(是否真正推送由
  config.NOTIFY_ON_REJECTION 开关控制,模块内部已处理,这里直接调用即可);
- 系统异常(采集器/提取器/校验器/通知器报错或未知异常)一律捕获后
  notifier.send_alert_message 告警;单条帖子的异常不能中断其它帖子的处理
  〔backend-api.md:定时任务 handler 必须幂等,外部调用失败要么重试要么明确告警〕;
- handler 本身不向 EventBridge 抛错,避免触发无意义的 Lambda 重试风暴,
  统一返回本轮执行的统计 dict,便于 CloudWatch Logs / 后续监控查阅。

多资产信号的 market 层判定(design.md「LLM 设计」双模型比对产出 common_assets,
可能不止一个资产):对 common_assets 逐个调用 market.get_snapshot,只要有一个
资产判定为「未被市场消化」(pricedIn=False)就放行进入下一层——因为帖子本身是
单一事件,任一目标资产还没被市场price-in,该事件对该资产仍有信号价值,不应
因为另一个资产已经异动就把整条信号一并否决;全部资产都已 price-in 时才否决。
每个资产的快照都完整落库（market_detail.snapshots），否决/通过原因都可追溯。
这一取舍在 verification 中向主流程说明，如与业务预期不符可按更保守规则调整。
"""

from __future__ import annotations

import logging
import traceback

import collector
import dedup
import extractor
import factcheck
import market
import notifier
import store
import validator

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# pipeline 阶段序号,决定 store.append_step 的 sk 排序(STEP#{seq:04d}#{step})
STEP_DEDUP = 1
STEP_EXTRACT = 2
STEP_VALIDATE = 3
STEP_MARKET = 4
STEP_FACTCHECK = 5
STEP_NOTIFY = 6


def lambda_handler(event, context):
    """Lambda 入口:跑一轮采集,对每个新帖执行完整 pipeline。

    不向 EventBridge 抛错(否则会按 EventBridge 的重试策略重放整个事件,
    可能重复告警/重复处理),异常统一转成告警消息 + 日志,返回统计 dict。
    """
    stats = {"fetched": 0, "processed": 0, "signals_sent": 0, "rejected": 0, "errors": 0}

    try:
        posts = collector.fetch_latest()
    except collector.CollectorError as exc:
        logger.error("app 采集失败(主/降级源均不可用): %s", exc)
        _alert("采集失败:主/降级源均不可用", str(exc))
        return stats
    except Exception as exc:  # noqa: BLE001 - 采集层未预料异常同样按系统异常告警,不让整轮执行崩掉
        logger.error("app 采集阶段未知异常: %s", type(exc).__name__)
        _alert("采集阶段未知异常", f"{type(exc).__name__}: {exc}")
        return stats

    stats["fetched"] = len(posts)

    for post in posts:
        try:
            _process_post(post, stats)
        except Exception as exc:  # noqa: BLE001 - 单帖异常不能中断其它帖子的处理
            stats["errors"] += 1
            logger.error("app 处理帖子异常 post_id=%s: %s", post.post_id, type(exc).__name__)
            _alert(
                f"处理帖子异常 post_id={post.post_id}",
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[:1500]}",
            )

    return stats


# ---------------------------------------------------------------------------
# 单帖 pipeline
# ---------------------------------------------------------------------------


def _process_post(post, stats: dict) -> None:
    """对一个新帖跑完整 pipeline;任一层否决直接 return 短路,不再往下走。"""
    stats["processed"] += 1

    # ---- Step 1: dedup(ID 去重 + 内容哈希/旧闻)----
    dedup_result = dedup.check(post.post_id, post.content, post.created_at)
    if dedup_result.verdict == "DUPLICATE_ID":
        # 每分钟轮询必然反复拉到已处理过的帖子,这是常规噪音而非新决策:
        # 绝不能对已有帖子再落 STEP/改 META 状态——那会把原帖的终态
        # (如 SIGNAL_SENT)覆写成 DUPLICATE 并覆盖原 dedup 决策记录,
        # 破坏 AC-010 的回查链路。静默跳过即可。
        stats["rejected"] += 1
        return
    if dedup_result.verdict != "NEW":
        stats["rejected"] += 1
        reason = dedup_result.detail.get("reason", dedup_result.verdict)
        _reject(
            post,
            STEP_DEDUP,
            "dedup",
            dedup_result.verdict,
            reason,
            dedup_result.detail,
            status="DUPLICATE",
        )
        return
    store.append_step(post.post_id, STEP_DEDUP, "dedup", "NEW", dedup_result.detail)
    store.update_post_status(post.post_id, "DEDUP_PASSED")

    # ---- Step 2: extractor(claude-fable-5 结构化提取)----
    try:
        extraction = extractor.extract(post.content, post.created_at)
    except extractor.ExtractionError as exc:
        _system_error(post, STEP_EXTRACT, "extract", exc, "信号提取失败")
        stats["errors"] += 1
        return

    extractor_signal = {
        "assets": extraction.assets,
        "direction": extraction.direction,
        "confidence": extraction.confidence,
        "reason": extraction.reason,
    }

    if extraction.is_irrelevant:
        stats["rejected"] += 1
        _reject(
            post,
            STEP_EXTRACT,
            "extract",
            "IRRELEVANT",
            extraction.reason or "与加密货币无关",
            extractor_signal,
        )
        return

    store.append_step(post.post_id, STEP_EXTRACT, "extract", "SIGNAL", extractor_signal)
    store.update_post_status(post.post_id, "EXTRACTED")

    # ---- Step 3: validator(claude-sonnet-5 独立判断 + 一致性比对)----
    try:
        validation = validator.validate(post, extractor_signal)
    except validator.ValidatorError as exc:
        _system_error(post, STEP_VALIDATE, "validate", exc, "双模型交叉验证失败")
        stats["errors"] += 1
        return

    if validation["agreement"] != "AGREE":
        stats["rejected"] += 1
        _reject(
            post,
            STEP_VALIDATE,
            "validate",
            "MODEL_DISAGREE",
            "双模型对资产/方向判断分歧",
            validation,
        )
        return

    store.append_step(post.post_id, STEP_VALIDATE, "validate", "AGREE", validation)
    store.update_post_status(post.post_id, "VALIDATED")

    common_assets = validation["common_assets"]
    direction = validation["validator_signal"]["direction"]

    # ---- Step 4: market(币安公开行情 price-in 检查,逐资产)----
    # market.get_snapshot 内部已 fail-closed 捕获 MarketError,不会向外抛异常。
    snapshots = [market.get_snapshot(asset, direction) for asset in common_assets]
    not_priced_in = [s for s in snapshots if not s["pricedIn"]]
    market_detail = {"snapshots": snapshots}

    # 行情接口不可用时 market 层折叠成 pricedIn=True(fail-closed),否决判定保持不变,
    # 但这本质是系统异常而非市场结论,必须额外告警,否则行情宕机被无声记成 PRICED_IN(review P1)
    unavailable = [s["asset"] for s in snapshots if not s.get("marketAvailable", True)]
    if unavailable:
        _alert(
            f"行情数据不可用 post_id={post.post_id}",
            f"资产 {', '.join(unavailable)} 的币安行情获取失败,已按 fail-closed 否决处理",
        )

    if not not_priced_in:
        stats["rejected"] += 1
        market_detail["reason"] = "全部目标资产短窗口内均已被市场消化(price-in)"
        _reject(post, STEP_MARKET, "market", "PRICED_IN", market_detail["reason"], market_detail)
        return

    store.append_step(post.post_id, STEP_MARKET, "market", "NOT_PRICED_IN", market_detail)
    store.update_post_status(post.post_id, "MARKET_PASSED")

    # ---- Step 5: factcheck(Claude web search 事实核查,fail-closed)----
    try:
        fc_result = factcheck.factcheck_post(post.content, post.created_at, post.url)
    except factcheck.FactCheckError as exc:
        # 核查调用失败按 fail-closed 处理为否决(design.md「事实核查」保守策略),
        # 同时也算一次系统异常,值得告警提醒人工关注(核查基建可能出问题了)。
        stats["rejected"] += 1
        detail = {"error": str(exc)}
        _reject(
            post,
            STEP_FACTCHECK,
            "factcheck",
            "FACT_CHECK_FAILED",
            f"核查调用失败,fail-closed 否决: {exc}",
            detail,
        )
        _alert(f"事实核查调用失败 post_id={post.post_id}", str(exc))
        return

    fc_detail = {"verdict": fc_result.verdict, "evidence": fc_result.evidence}
    if not fc_result.passed:
        stats["rejected"] += 1
        _reject(
            post,
            STEP_FACTCHECK,
            "factcheck",
            "FACT_CHECK_FAILED",
            f"事实核查未通过: verdict={fc_result.verdict}",
            fc_detail,
        )
        return

    store.append_step(post.post_id, STEP_FACTCHECK, "factcheck", "CONFIRMED", fc_detail)
    store.update_post_status(post.post_id, "FACTCHECK_PASSED")

    # ---- Step 6: notifier(全部通过,推送信号)----
    market_summary = "; ".join(
        f"{s['asset']}={s['currentPrice']}({'已消化' if s['pricedIn'] else '未消化'})" for s in snapshots
    )
    notify_detail = {"assets": common_assets, "direction": direction, "marketSummary": market_summary}
    try:
        notifier.send_signal_message(
            post_summary=post.content[:280],
            post_url=post.url,
            assets=common_assets,
            direction=direction,
            extractor_confidence=extractor_signal["confidence"],
            validator_confidence=validation["validator_signal"]["confidence"],
            market_snapshot=market_summary,
            factcheck_verdict=fc_result.verdict,
        )
    except notifier.NotifierError as exc:
        stats["errors"] += 1
        store.append_step(post.post_id, STEP_NOTIFY, "notify", "ERROR", {**notify_detail, "error": str(exc)})
        store.update_post_status(post.post_id, "ERROR")
        _alert(f"信号推送失败 post_id={post.post_id}", str(exc))
        return

    stats["signals_sent"] += 1
    store.append_step(post.post_id, STEP_NOTIFY, "notify", "SENT", notify_detail)
    store.update_post_status(post.post_id, "SIGNAL_SENT")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _reject(post, seq: int, step: str, result: str, reason: str, detail: dict | None = None, status: str | None = None) -> None:
    """否决层通用处理:落一条 STEP 决策记录 + 更新帖子状态 + (按开关)发否决消息。

    status 单独可传(如 dedup 的 DUPLICATE_ID/STALE_CONTENT 两种细分 verdict
    在帖子 META 上统一记成 AC-003 约定的 DUPLICATE),默认与 result 相同。
    """
    detail = dict(detail or {})
    detail.setdefault("reason", reason)
    store.append_step(post.post_id, seq, step, result, detail)
    store.update_post_status(post.post_id, status or result)
    try:
        notifier.send_rejection_message(step=step, reason=reason, post_url=post.url)
    except notifier.NotifierError as exc:
        # 否决消息发送失败不应影响否决判定本身已经落库,只记日志,不算系统异常告警
        logger.warning("app 否决消息发送失败 post_id=%s step=%s: %s", post.post_id, step, exc)


def _system_error(post, seq: int, step: str, exc: Exception, summary: str) -> None:
    """系统异常(LLM/网络类)通用处理:落 ERROR 决策记录 + 更新状态 + 告警。

    与 _reject 区分开:这类异常不是「验证层给出了否决结论」,而是「本该跑的
    验证根本没跑成」,不应误判为某种业务否决,单独记 ERROR 状态并告警提醒人工介入。
    """
    logger.error("app %s 阶段异常 post_id=%s: %s", step, post.post_id, exc)
    store.append_step(post.post_id, seq, step, "ERROR", {"error": str(exc)})
    store.update_post_status(post.post_id, "ERROR")
    _alert(f"{summary} post_id={post.post_id}", str(exc))


def _alert(summary: str, detail: str) -> None:
    """发送系统告警;告警本身发送失败只记日志,不能再抛异常导致告警链路自我崩溃。"""
    try:
        notifier.send_alert_message(summary=summary, detail=detail)
    except notifier.NotifierError as exc:
        logger.error("app 告警消息发送失败: %s", exc)
