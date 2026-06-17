"""
test_auth.py —— user-auth v1 的正式测试

把开发过程中的临时自测固化下来,随时可重跑:
    cd /Users/Admin/Documents/claude
    ./.venv/bin/pip install -r requirements-dev.txt   # 首次
    AWS_DEFAULT_REGION=us-east-1 ./.venv/bin/python -m pytest -q

测试分两层:
- 纯函数(validators / security): 不需要数据库,直接测。〔T9.1〕
- 业务/入口(handlers / app): 用 moto 模拟 DynamoDB 跑全链路。〔T9.2〕
"""

import sys, os, json
import datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auth"))

import pytest
import boto3
from moto import mock_aws
import jwt as pyjwt


# ---------------- 纯函数:validators〔Req 1.1/1.2/4.4〕----------------

import validators as v

def test_email_validation():
    assert v.is_valid_email("a@x.com")
    assert not v.is_valid_email("bad-email")
    assert not v.is_valid_email("a b@x.com")

def test_password_strength():
    assert v.is_strong_password("abc12345")
    assert not v.is_strong_password("short1")     # 太短
    assert not v.is_strong_password("abcdefgh")   # 无数字
    assert not v.is_strong_password("12345678")   # 无字母

def test_normalize_email():
    assert v.normalize_email("  A@X.COM ") == "a@x.com"


# ---------------- 纯函数:security〔Req 4.1/2.4/3.3〕----------------

import security as sec

def test_password_hash_roundtrip():
    h = sec.hash_password("abc12345")
    assert h != "abc12345"                      # 非明文
    assert sec.verify_password("abc12345", h)   # 正确密码匹配
    assert not sec.verify_password("wrong", h)  # 错误密码不匹配
    assert sec.hash_password("abc12345") != h   # 盐随机

def test_token_roundtrip():
    t = sec.make_token("a@x.com")
    assert sec.parse_token(t) == "a@x.com"

def test_token_tampered():
    with pytest.raises(sec.AuthError):
        sec.parse_token(sec.make_token("a@x.com") + "x")

def test_token_expired():
    expired = pyjwt.encode(
        {"sub": "a@x.com", "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)},
        sec.JWT_SECRET, algorithm=sec.JWT_ALGO)
    with pytest.raises(sec.AuthError):
        sec.parse_token(expired)


# ---------------- 业务/入口:用 moto 模拟 DynamoDB〔Req 1/2/3 闭环〕----------------

@pytest.fixture
def mocked_aws():
    """每个测试一张干净的假表。"""
    with mock_aws():
        boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="Users",
            KeySchema=[{"AttributeName": "email", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "email", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        yield


def _event(path, method, body=None, headers=None):
    return {"path": path, "httpMethod": method,
            "body": json.dumps(body) if body else None,
            "headers": headers or {}}


def test_register_flow(mocked_aws):
    import handlers as h
    assert h.handle_register({"email": "A@X.com", "password": "abc12345"})[0] == 201
    assert h.handle_register({"email": "a@x.com", "password": "abc12345"})[0] == 409  # 重复(规范化)
    assert h.handle_register({"email": "bad", "password": "abc12345"})[0] == 422      # 邮箱非法
    assert h.handle_register({"email": "b@x.com", "password": "short"})[0] == 422     # 密码弱
    assert h.handle_register({"email": "b@x.com"})[0] == 422                          # 缺字段
    body = h.handle_register({"email": "b@x.com", "password": "pass1234"})[1]
    assert "passwordHash" not in body["user"]                                        # 不含密码


def test_login_anti_enumeration(mocked_aws):
    import handlers as h
    h.handle_register({"email": "a@x.com", "password": "abc12345"})
    assert h.handle_login({"email": "a@x.com", "password": "abc12345"})[0] == 200
    # 密码错 与 邮箱不存在 -> 完全相同的提示〔Req 2.3〕
    wrong_pw = h.handle_login({"email": "a@x.com", "password": "nope"})
    no_user  = h.handle_login({"email": "ghost@x.com", "password": "abc12345"})
    assert wrong_pw[0] == no_user[0] == 401
    assert wrong_pw[1] == no_user[1]


def test_me_requires_valid_token(mocked_aws):
    import handlers as h
    h.handle_register({"email": "a@x.com", "password": "abc12345"})
    token = h.handle_login({"email": "a@x.com", "password": "abc12345"})[1]["token"]
    assert h.handle_me({"Authorization": "Bearer " + token})[0] == 200
    assert h.handle_me({})[0] == 401                        # 无 token
    assert h.handle_me({"Authorization": "Bearer junk"})[0] == 401  # 无效 token


def test_app_end_to_end(mocked_aws):
    """通过 Lambda 入口跑完整闭环:注册 -> 登录 -> /me。〔T9.2〕"""
    import app
    def call(path, method, body=None, headers=None):
        r = app.lambda_handler(_event(path, method, body, headers), None)
        return r["statusCode"], json.loads(r["body"]), r["headers"]

    sc, _, hd = call("/Prod/register", "POST", {"email": "a@x.com", "password": "abc12345"})
    assert sc == 201 and hd["Access-Control-Allow-Origin"] == "*"

    sc, b, _ = call("/Prod/login", "POST", {"email": "a@x.com", "password": "abc12345"})
    assert sc == 200
    token = b["token"]

    sc, b, _ = call("/Prod/me", "GET", headers={"Authorization": "Bearer " + token})
    assert sc == 200 and b["email"] == "a@x.com"

    assert call("/Prod/login", "OPTIONS")[0] == 200   # CORS 预检
    assert call("/Prod/nope", "GET")[0] == 404        # 未知路由


# ---------------- change-password〔feature 2〕----------------

def test_change_password_flow(mocked_aws):
    """修改密码全链路 + 各失败分支。〔2.AC-001~AC-005〕"""
    import handlers as h
    h.handle_register({"email": "a@x.com", "password": "abc12345"})
    token = h.handle_login({"email": "a@x.com", "password": "abc12345"})[1]["token"]
    auth = {"Authorization": "Bearer " + token}

    # 无 token → 401〔AC-002〕
    assert h.handle_change_password({}, {"oldPassword": "abc12345", "newPassword": "xyz98765"})[0] == 401
    # 旧密码错 → 401〔AC-003〕
    assert h.handle_change_password(auth, {"oldPassword": "wrongpw1", "newPassword": "xyz98765"})[0] == 401
    # 新密码弱 → 422〔AC-004〕
    assert h.handle_change_password(auth, {"oldPassword": "abc12345", "newPassword": "weak"})[0] == 422
    # 新旧相同 → 422〔AC-005〕
    assert h.handle_change_password(auth, {"oldPassword": "abc12345", "newPassword": "abc12345"})[0] == 422

    # 成功 → 200〔AC-001〕
    assert h.handle_change_password(auth, {"oldPassword": "abc12345", "newPassword": "xyz98765"})[0] == 200
    # 旧密码失效、新密码可登录
    assert h.handle_login({"email": "a@x.com", "password": "abc12345"})[0] == 401
    assert h.handle_login({"email": "a@x.com", "password": "xyz98765"})[0] == 200


def test_change_password_stale_concurrent(mocked_aws):
    """并发改密:用过期旧哈希做 compare-and-swap 必须失败。〔Codex P2〕"""
    import store, security as sec
    store.create_user("a@x.com", sec.hash_password("abc12345"))
    user = store.get_user("a@x.com")
    old_hash = user["passwordHash"]

    # 第一个请求成功改密
    store.update_password("a@x.com", sec.hash_password("xyz98765"), old_hash)

    # 第二个并发请求拿着已失效的 old_hash → StalePassword
    import pytest as _pytest
    with _pytest.raises(store.StalePassword):
        store.update_password("a@x.com", sec.hash_password("other123"), old_hash)


def test_change_password_via_app(mocked_aws):
    """通过 Lambda 入口验证路由接通。"""
    import app
    def call(path, method, body=None, headers=None):
        r = app.lambda_handler(_event(path, method, body, headers), None)
        return r["statusCode"], json.loads(r["body"])
    call("/Prod/register", "POST", {"email": "a@x.com", "password": "abc12345"})
    token = call("/Prod/login", "POST", {"email": "a@x.com", "password": "abc12345"})[1]["token"]
    sc, b = call("/Prod/change-password", "POST",
                 {"oldPassword": "abc12345", "newPassword": "xyz98765"},
                 {"Authorization": "Bearer " + token})
    assert sc == 200 and "message" in b
