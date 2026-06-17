"""
handlers.py —— 三个业务处理函数

把前面写好的 validators / security / store 串起来,
组成"注册、登录、看我的信息"三段完整业务。

每个 handler 返回 (status_code, body_dict) 这样一个二元组,
由 app.py 再包装成 API Gateway 要的响应格式。
"""

import validators as v
import security as sec
import store


def handle_register(body: dict):
    """
    注册:校验 -> 哈希 -> 写库 -> 返回用户(不含密码)。〔Req 1.1-1.8〕
    """
    email = body.get("email")
    password = body.get("password")

    # 1. 字段缺失〔Req 1.6/2.5 风格的字段级错误〕
    if not email or not password:
        return 422, {"error": "邮箱和密码不能为空"}

    # 2. 邮箱格式〔Req 1.1/1.6〕
    if not v.is_valid_email(email):
        return 422, {"error": "邮箱格式不正确", "field": "email"}

    # 3. 密码强度〔Req 1.2/1.7〕
    if not v.is_strong_password(password):
        return 422, {"error": "密码至少8位且含字母和数字", "field": "password"}

    email = v.normalize_email(email)  # 规范化〔Req 4.4〕

    # 4. 哈希 + 写库(写库自带防重复)〔Req 1.3/4.1〕
    pw_hash = sec.hash_password(password)
    try:
        user = store.create_user(email, pw_hash)
    except store.EmailExists:
        return 409, {"error": "邮箱已存在"}  # 〔Req 1.5〕

    # 5. 返回(不含密码)〔Req 1.8〕
    return 201, {"user": store.public_view(user)}


def handle_login(body: dict):
    """
    登录:查用户 -> 验密码 -> 签 token。〔Req 2.1-2.5〕
    失败统一返回"邮箱或密码错误",不区分是邮箱还是密码错(防账号枚举)。〔Req 2.3〕
    """
    email = body.get("email")
    password = body.get("password")

    if not email or not password:
        return 422, {"error": "邮箱和密码不能为空"}  # 〔Req 2.5〕

    email = v.normalize_email(email)
    user = store.get_user(email)  # 〔Req 2.1〕

    # 邮箱不存在 或 密码不匹配 -> 同一句提示〔Req 2.3〕
    if user is None or not sec.verify_password(password, user["passwordHash"]):
        return 401, {"error": "邮箱或密码错误"}

    token = sec.make_token(email)  # 〔Req 2.2〕
    return 200, {"token": token}


def handle_me(headers: dict):
    """
    当前用户:从 Authorization 头取 token -> 校验 -> 返回信息。〔Req 3.1-3.3〕
    """
    # HTTP 头大小写不敏感,做个兼容
    auth = headers.get("Authorization") or headers.get("authorization") or ""

    # 期望格式: "Bearer <token>"
    if not auth.startswith("Bearer "):
        return 401, {"error": "未认证"}  # 〔Req 3.2〕
    token = auth[len("Bearer "):].strip()

    try:
        email = sec.parse_token(token)  # 〔Req 3.3〕
    except sec.AuthError:
        return 401, {"error": "未认证"}

    user = store.get_user(email)
    if user is None:
        # token 有效但用户已被删除等极端情况
        return 401, {"error": "未认证"}

    return 200, store.public_view(user)  # 〔Req 3.1〕
