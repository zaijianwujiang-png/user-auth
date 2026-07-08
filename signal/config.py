# -*- coding: utf-8 -*-
"""config.py —— 环境变量配置集中读取。

`signal/` 下所有模块的可调参数(密钥、表名、阈值)都从这里读,
不允许在业务模块里散落 `os.environ.get(...)`——
换一套部署参数只需要改 template.yaml 的 Environment.Variables,不用改代码。

密钥类(ANTHROPIC_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)由
template.yaml 的 SAM NoEcho Parameters 注入,本模块只读不打日志〔security.md〕。
阈值类给了 v1 合理默认值,均可被环境变量覆盖,方便不重新部署代码就调参。
"""

import os

# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------

# 单表名,由 template.yaml 的 SignalTable 注入(T-001)
SIGNAL_TABLE_NAME = os.environ.get("SIGNAL_TABLE_NAME", "SignalTable")

# ---------------------------------------------------------------------------
# 外部服务密钥(NoEcho 参数注入,严禁打日志)
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# 阈值与开关(均可被环境变量覆盖,默认值取 design.md 约定)
# ---------------------------------------------------------------------------

# 采集源连续失败多少次后,由主源(Truth Social API)自动切到降级源(RSS 镜像)〔design.md 采集方案〕
SOURCE_FAIL_THRESHOLD = int(os.environ.get("SOURCE_FAIL_THRESHOLD", "3"))

# price-in 判定阈值:信号发布后价格已朝该方向变动超过此百分比,视为"已被市场消化",否决信号
PRICE_IN_THRESHOLD_PCT = float(os.environ.get("PRICE_IN_THRESHOLD_PCT", "0.03"))

# 旧闻窗口:同一内容哈希在此天数内出现过,视为旧闻/重复,直接否决〔数据模型 HASH 索引〕
STALE_NEWS_WINDOW_DAYS = int(os.environ.get("STALE_NEWS_WINDOW_DAYS", "30"))

# 帖子 META 记录的 TTL(天),到期由 DynamoDB 自动清理
POST_TTL_DAYS = int(os.environ.get("POST_TTL_DAYS", "90"))

# 内容哈希索引的 TTL(天)——语义上应与旧闻窗口一致(窗口外的旧内容不必再占用索引),
# 但独立开出环境变量以便按需单独调整而不影响旧闻判定窗口本身
HASH_TTL_DAYS = int(os.environ.get("HASH_TTL_DAYS", str(STALE_NEWS_WINDOW_DAYS)))

# 否决消息(dedup/extract/validate/market/factcheck 任一层否决时)是否推送 Telegram,
# 默认关闭以免噪音过大;需要人工复盘否决原因时可打开〔design.md 接口契约:否决消息〕
NOTIFY_ON_REJECTION = os.environ.get("NOTIFY_ON_REJECTION", "false").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# 采集器(signal/collector.py,主源 + 降级源共用的网络/抓取参数)
# ---------------------------------------------------------------------------

# 单次 HTTP 请求超时(秒)
COLLECTOR_TIMEOUT_SECONDS = float(os.environ.get("COLLECTOR_TIMEOUT_SECONDS", "10"))

# 单个源的最大重试次数(网络错误/429/5xx 触发重试)
COLLECTOR_MAX_RETRIES = int(os.environ.get("COLLECTOR_MAX_RETRIES", "3"))

# 主源单次拉取条数
COLLECTOR_FETCH_LIMIT = int(os.environ.get("COLLECTOR_FETCH_LIMIT", "20"))

# 重试首次退避基数(秒),之后指数翻倍并加抖动;Lambda 每分钟触发一次,总重试耗时必须远小于 60s
COLLECTOR_BACKOFF_BASE_SECONDS = float(os.environ.get("COLLECTOR_BACKOFF_BASE_SECONDS", "1"))

# 主源基础 URL(便于测试/自建镜像替换)
TRUTH_SOCIAL_BASE_URL = os.environ.get("TRUTH_SOCIAL_BASE_URL", "https://truthsocial.com")

# 降级源:第三方 RSS 镜像站(design.md 方案 B),提供 @realDonaldTrump 帖子存档
RSS_MIRROR_URL = os.environ.get("RSS_MIRROR_URL", "https://trumpstruth.org/feed")

# 单次 HTTP 响应体大小上限(字节),防止异常/恶意响应把 Lambda 内存占爆;超限按源失败处理
COLLECTOR_MAX_RESPONSE_BYTES = int(os.environ.get("COLLECTOR_MAX_RESPONSE_BYTES", str(5 * 1024 * 1024)))
