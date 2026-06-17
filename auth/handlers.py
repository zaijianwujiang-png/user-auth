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


def _email_from_headers(headers: dict):
    """
    从 Authorization 头解析出已认证用户的 email。〔Req 3.2/3.3〕
    成功返回 email;无 token / 格式错 / token 无效或过期 → 返回 None。
    handle_me 和 handle_change_password 共用,避免重复。
    """
    # HTTP 头大小写不敏感,做个兼容
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):].strip()
    try:
        return sec.parse_token(token)
    except sec.AuthError:
        return None


def handle_me(headers: dict):
    """
    当前用户:从 Authorization 头取 token -> 校验 -> 返回信息。〔Req 3.1-3.3〕
    """
    email = _email_from_headers(headers)
    if email is None:
        return 401, {"error": "未认证"}  # 〔Req 3.2/3.3〕

    user = store.get_user(email)
    if user is None:
        # token 有效但用户已被删除等极端情况
        return 401, {"error": "未认证"}

    return 200, store.public_view(user)  # 〔Req 3.1〕


def handle_change_password(headers: dict, body: dict):
    """
    修改密码:验 token -> 验旧密码 -> 校验新密码 -> 更新。〔change-password F-001~F-006〕
    """
    email = _email_from_headers(headers)
    if email is None:
        return 401, {"error": "未认证"}  # 〔F-002〕

    old = body.get("oldPassword")
    new = body.get("newPassword")
    if not old or not new:
        return 422, {"error": "旧密码和新密码不能为空"}

    user = store.get_user(email)
    # 用户不存在 或 旧密码不匹配 → 统一 401(不泄露细节)〔F-003〕
    if user is None or not sec.verify_password(old, user["passwordHash"]):
        return 401, {"error": "旧密码错误"}

    if not v.is_strong_password(new):  # 〔F-004〕
        return 422, {"error": "密码至少8位且含字母和数字", "field": "newPassword"}

    if old == new:  # 〔F-006〕
        return 422, {"error": "新密码不能与旧密码相同"}

    # 带上已验证的旧哈希做 compare-and-swap,防并发改密的覆盖竞态〔Codex P2〕
    try:
        store.update_password(email, sec.hash_password(new), user["passwordHash"])
    except store.StalePassword:
        return 409, {"error": "密码刚被修改过,请重新登录后再试"}
    return 200, {"message": "密码已更新"}
