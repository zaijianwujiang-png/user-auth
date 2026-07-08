# -*- coding: utf-8 -*-
"""
test_signal.py —— trump-signal-trader v1 的正式测试(T-012)

跑法(Lambda 运行时是 3.12,store.py 用了 3.10+ 语法,3.9 的 .venv 跑不了):
    cd /Users/Admin/Documents/claude
    python3.12 -m venv .venv312 && ./.venv312/bin/pip install -r requirements-dev.txt
    AWS_DEFAULT_REGION=us-east-1 ./.venv312/bin/python -m pytest tests/test_signal.py -q

分层:
- 纯函数(dedup 规范化/哈希): 不需要数据库直接测。
- dedup/store: moto 模拟 DynamoDB,验证条件写幂等与旧闻窗口。
- pipeline(app.lambda_handler): moto + monkeypatch 掉全部外呼(采集/LLM/行情/核查/Telegram),
  逐层验证否决短路、全过发信号、决策链路回查(AC-010)。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signal"))

# config.py 在 import 时读环境变量,必须先设好再 import 被测模块
os.environ.setdefault("SIGNAL_TABLE_NAME", "SignalTableTest")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

import boto3
import pytest
from moto import mock_aws

# signal/ 源码用了 3.10+ 语法,3.9 的 .venv 下整个文件跳过,避免收集报错
if sys.version_info < (3, 10):
    pytest.skip("signal/ 需要 python>=3.10(Lambda 运行时 3.12)", allow_module_level=True)

import app
import config
import dedup
import store
from collector import Post


# ---------------- 纯函数:内容规范化与哈希〔AC-003〕 ----------------


def test_normalize_collapses_variants():
    a = "HUGE news!!! Bitcoin to the MOON  https://t.co/abc123"
    b = "huge news bitcoin to the moon"
    assert dedup.normalize_content(a) == dedup.normalize_content(b)
    assert dedup.compute_content_hash(a) == dedup.compute_content_hash(b)


def test_different_content_different_hash():
    assert dedup.compute_content_hash("buy bitcoin") != dedup.compute_content_hash("sell bitcoin")


# ---------------- 纯函数:notifier 转义与链接安全(review P0 回归) ----------------


def test_notifier_escapes_quotes_for_attribute_context():
    import notifier

    escaped = notifier._esc('x" onclick="evil')
    assert '"' not in escaped  # 双引号必须被转义,否则逃逸 href="..." 属性注入 HTML


def test_notifier_link_line_rejects_non_http_schemes():
    import notifier

    assert notifier._link_line("https://truthsocial.com/x/1") is not None
    assert notifier._link_line("javascript:alert(1)") is None
    assert notifier._link_line("") is None


def test_factcheck_worst_case_fits_lambda_budget():
    """最坏 2 次完整 API 调用(首次+解析重试)必须留在 Lambda 120s 预算内(review P0)。"""
    import factcheck

    assert 2 * factcheck.DEFAULT_TIMEOUT_SECONDS <= 110


# ---------------- 基建:moto 表 + 外呼打桩 ----------------


def _create_table():
    """按 template.yaml 的 SignalTable 定义建表(pk/sk 组合主键)。"""
    boto3.client("dynamodb").create_table(
        TableName=config.SIGNAL_TABLE_NAME,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
    )
    # store.py 的 boto3 资源句柄是模块级缓存,moto 环境下要重建才指向 mock
    store._dynamodb = boto3.resource("dynamodb")


@pytest.fixture()
def table():
    with mock_aws():
        _create_table()
        yield


def _post(post_id="p1", content="Bitcoin is going to be HUGE, buy now!", url="https://truthsocial.com/x/1"):
    return Post(post_id=post_id, content=content, created_at="2026-07-07T12:00:00+00:00", url=url, source="primary", raw={})


class FakeExtraction:
    """extractor.ExtractionResult 的最小替身(只需 pipeline 用到的字段)。"""

    def __init__(self, assets, direction, confidence=0.9, reason="test"):
        self.assets = assets
        self.direction = direction
        self.confidence = confidence
        self.reason = reason

    @property
    def is_irrelevant(self):
        return self.direction == "irrelevant" or not self.assets


class FakeFactCheck:
    def __init__(self, verdict):
        self.verdict = verdict
        self.evidence = [{"summary": "test", "source": "https://example.com"}]
        self.passed = verdict == "confirmed"


@pytest.fixture()
def pipeline(table, monkeypatch):
    """打桩全部外呼,返回可按用例调整的『旋钮』dict;默认全通过路径。"""
    knobs = {
        "posts": [_post()],
        "extraction": FakeExtraction(["BTC"], "bullish"),
        "validator": {"agreement": "AGREE", "common_assets": ["BTC"],
                      "extractor_signal": {"direction": "bullish", "confidence": 0.9},
                      "validator_signal": {"direction": "bullish", "confidence": 0.8}},
        "priced_in": {},          # asset -> bool,缺省 False(未消化)
        "factcheck": FakeFactCheck("confirmed"),
        "sent_signals": [],       # 捕获 send_signal_message 调用
        "rejections": [],         # 捕获 send_rejection_message 调用
        "alerts": [],             # 捕获 send_alert_message 调用
    }

    def fake_snapshot(asset, direction):
        return {"asset": asset, "symbol": f"{asset}USDT", "direction": direction,
                "marketAvailable": True, "currentPrice": 100.0, "change24hPct": 1.0,
                "shortWindowChangePct": 0.5, "shortWindowMinutes": 15, "volume24h": 1.0,
                "pricedIn": knobs["priced_in"].get(asset, False), "reason": "test"}

    monkeypatch.setattr(app.collector, "fetch_latest", lambda: knobs["posts"])
    monkeypatch.setattr(app.extractor, "extract", lambda content, created_at: knobs["extraction"])
    monkeypatch.setattr(app.validator, "validate", lambda post, sig: knobs["validator"])
    monkeypatch.setattr(app.market, "get_snapshot", fake_snapshot)
    monkeypatch.setattr(app.factcheck, "factcheck_post", lambda content, created_at, url: knobs["factcheck"])
    monkeypatch.setattr(app.notifier, "send_signal_message",
                        lambda **kw: knobs["sent_signals"].append(kw))
    monkeypatch.setattr(app.notifier, "send_rejection_message",
                        lambda **kw: knobs["rejections"].append(kw))
    monkeypatch.setattr(app.notifier, "send_alert_message",
                        lambda **kw: knobs["alerts"].append(kw))
    return knobs


def _status(post_id):
    return store.get_post(post_id)["status"]


# ---------------- dedup:ID 幂等 + 旧闻窗口〔AC-002/AC-003〕 ----------------


def test_dedup_id_idempotent(table):
    r1 = dedup.check("p1", "hello world", "2026-07-07T00:00:00+00:00")
    assert r1.verdict == "NEW"
    r2 = dedup.check("p1", "hello world", "2026-07-07T00:00:00+00:00")
    assert r2.verdict == "DUPLICATE_ID"


def test_dedup_stale_content_window(table):
    r1 = dedup.check("p1", "Bitcoin will be HUGE! https://t.co/a", "2026-07-07T00:00:00+00:00")
    assert r1.verdict == "NEW"
    # 新 ID、规范化后同内容 → 旧闻
    r2 = dedup.check("p2", "bitcoin will be huge", "2026-07-07T01:00:00+00:00")
    assert r2.verdict == "STALE_CONTENT"


# ---------------- pipeline:逐层否决短路 ----------------


def test_irrelevant_short_circuits(pipeline):
    pipeline["extraction"] = FakeExtraction([], "irrelevant")
    stats = app.lambda_handler({}, None)
    assert stats == {"fetched": 1, "processed": 1, "signals_sent": 0, "rejected": 1, "errors": 0}
    assert _status("p1") == "IRRELEVANT"
    assert pipeline["sent_signals"] == []


def test_model_disagree_rejects(pipeline):
    pipeline["validator"] = {"agreement": "DISAGREE", "common_assets": [],
                             "extractor_signal": {}, "validator_signal": {}}
    app.lambda_handler({}, None)
    assert _status("p1") == "MODEL_DISAGREE"
    assert pipeline["sent_signals"] == []


def test_all_assets_priced_in_rejects(pipeline):
    pipeline["validator"]["common_assets"] = ["BTC", "DOGE"]
    pipeline["priced_in"] = {"BTC": True, "DOGE": True}
    app.lambda_handler({}, None)
    assert _status("p1") == "PRICED_IN"


def test_partial_priced_in_passes(pipeline):
    """T-011 决策:任一资产未消化即放行(全部 price-in 才否决)。"""
    pipeline["validator"]["common_assets"] = ["BTC", "DOGE"]
    pipeline["priced_in"] = {"BTC": True, "DOGE": False}
    stats = app.lambda_handler({}, None)
    assert stats["signals_sent"] == 1
    assert _status("p1") == "SIGNAL_SENT"


def test_factcheck_unverified_rejects(pipeline):
    pipeline["factcheck"] = FakeFactCheck("unverified")
    app.lambda_handler({}, None)
    assert _status("p1") == "FACT_CHECK_FAILED"
    assert pipeline["sent_signals"] == []


# ---------------- pipeline:全通过发信号 + 全链路回查〔AC-009/AC-010〕 ----------------


def test_all_pass_sends_signal_and_chain(pipeline):
    stats = app.lambda_handler({}, None)
    assert stats["signals_sent"] == 1
    assert _status("p1") == "SIGNAL_SENT"
    assert len(pipeline["sent_signals"]) == 1
    sent = pipeline["sent_signals"][0]
    assert sent["assets"] == ["BTC"] and sent["direction"] == "bullish"

    # AC-010:一次 query 回查完整决策链
    chain = store.get_chain("p1")
    assert chain["post"]["status"] == "SIGNAL_SENT"
    steps = [(s["step"], s["result"]) for s in chain["steps"]]
    assert steps == [("dedup", "NEW"), ("extract", "SIGNAL"), ("validate", "AGREE"),
                     ("market", "NOT_PRICED_IN"), ("factcheck", "CONFIRMED"), ("notify", "SENT")]
    # detail 是 JSON 字符串且可解析
    assert isinstance(json.loads(chain["steps"][0]["detail"]), dict)


def test_recollected_post_does_not_clobber_history(pipeline):
    """回归:已处理过的帖子被下一轮轮询重复拉到时,绝不能覆写原 META 状态与 STEP 记录。"""
    app.lambda_handler({}, None)
    assert _status("p1") == "SIGNAL_SENT"
    chain_before = store.get_chain("p1")

    stats = app.lambda_handler({}, None)  # 第二轮拉到同一帖
    assert stats["rejected"] == 1 and stats["signals_sent"] == 0
    assert _status("p1") == "SIGNAL_SENT"          # 终态没被改成 DUPLICATE
    assert store.get_chain("p1") == chain_before   # 决策链原封不动
    assert len(pipeline["sent_signals"]) == 1      # 没有重复发信号


# ---------------- collector:源切换告警〔AC-009〕 ----------------


def test_source_switch_sends_alert(table, monkeypatch):
    import collector as collector_mod
    import notifier

    alerts = []
    monkeypatch.setattr(notifier, "send_alert_message", lambda **kw: alerts.append(kw))
    monkeypatch.setattr(
        collector_mod.TruthSocialApiSource, "fetch_latest",
        lambda self: (_ for _ in ()).throw(collector_mod.CollectorError("403")),
    )
    monkeypatch.setattr(collector_mod.RssMirrorSource, "fetch_latest", lambda self: [])
    # 预置失败计数到阈值-1,本轮失败即触发切换
    store.put_source_state(active_source="primary", fail_count=config.SOURCE_FAIL_THRESHOLD - 1)

    posts = collector_mod.fetch_latest()
    assert posts == []
    assert store.get_source_state()["activeSource"] == "fallback"
    assert len(alerts) == 1 and "切换" in alerts[0]["summary"]  # AC-009:切换必须告警


# ---------------- pipeline:系统异常隔离与告警 ----------------


def test_extraction_error_alerts_not_blocks_batch(pipeline, monkeypatch):
    import extractor as extractor_mod

    p_bad, p_good = _post("bad"), _post("good", content="Totally different news about ETH!")

    def extract(content, created_at):
        if "different" in content:
            return FakeExtraction(["ETH"], "bullish")
        raise extractor_mod.ExtractionError("boom")

    pipeline["posts"] = [p_bad, p_good]
    monkeypatch.setattr(app.extractor, "extract", extract)
    stats = app.lambda_handler({}, None)
    assert stats["errors"] == 1 and stats["signals_sent"] == 1   # 坏帖不拖累好帖
    assert _status("bad") == "ERROR"
    assert len(pipeline["alerts"]) == 1


def test_collector_failure_alerts(pipeline, monkeypatch):
    import collector as collector_mod

    def boom():
        raise collector_mod.CollectorError("both sources down")

    monkeypatch.setattr(app.collector, "fetch_latest", boom)
    stats = app.lambda_handler({}, None)
    assert stats == {"fetched": 0, "processed": 0, "signals_sent": 0, "rejected": 0, "errors": 0}
    assert len(pipeline["alerts"]) == 1
