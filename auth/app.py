"""
app.py —— Lambda 入口 + 路由

每当有人访问 /register /login /me,API Gateway 就调用 lambda_handler 一次。
这里负责:
  1. 解析请求(路径、方法、body、headers)
  2. 按"路径 + 方法"分发给对应的 handler
  3. 把 handler 返回的 (状态码, 数据) 包装成 API Gateway 要的格式(带 CORS 头)
"""

import json

import handlers


# 统一的 CORS 头:允许任意网页调用本接口(沿用 lambda-demo 约定)
CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}


def _response(status_code: int, body: dict):
    """把 (状态码, 字典) 包装成 API Gateway 的标准响应。〔§8〕"""
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, ensure_ascii=False),
    }


def _parse_body(event: dict) -> dict:
    """把请求 body(字符串)解析成字典;解析失败返回空字典。"""
    raw = event.get("body")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def lambda_handler(event, context):
    """Lambda 的入口函数。〔§4 / T6.1〕"""
    path = event.get("path", "")
    method = (event.get("httpMethod") or "").upper()
    headers = event.get("headers") or {}

    try:
        # 浏览器跨域会先发 OPTIONS 预检,直接回 200〔CORS〕
        if method == "OPTIONS":
            return _response(200, {})

        # ---- 路由表:路径 + 方法 -> handler ----
        if path.endswith("/register") and method == "POST":
            status, body = handlers.handle_register(_parse_body(event))
        elif path.endswith("/login") and method == "POST":
            status, body = handlers.handle_login(_parse_body(event))
        elif path.endswith("/me") and method == "GET":
            status, body = handlers.handle_me(headers)
        else:
            status, body = 404, {"error": "接口不存在"}

        return _response(status, body)

    except Exception as e:  # 兜底:任何未预料的异常都返回 500〔§8〕
        # 注意:只打印异常类型,不打印 body(可能含密码/token)〔Req 4.3〕
        print(f"[ERROR] {type(e).__name__}: {e}")
        return _response(500, {"error": "服务器内部错误"})
