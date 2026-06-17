"""
security.py —— 密码加密 与 登录令牌

两件安全大事:
1. 密码:用 bcrypt 做"加盐哈希",数据库里只存哈希值,原始密码永不落库。〔Req 4.1〕
2. 令牌:用 JWT 签发一张"身份凭证",有过期时间,证明请求者是谁。〔Req 2.4〕
"""

import os
import datetime as dt

import bcrypt
import jwt  # 来自 PyJWT 包

# JWT 签名密钥:从环境变量读,SAM 模板里配置。
# 本地没配时给个默认值,方便调试(生产一定要在环境变量里设真实值)。
# 默认值刻意 >=32 字节(JWT/SHA256 的推荐下限);生产务必用环境变量覆盖成随机强密钥
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me-0123456789abcdef")
JWT_ALGO = "HS256"
TOKEN_TTL_HOURS = 24  # 令牌有效期 24 小时〔Req 2.4〕


# ---------- 密码 ----------

def hash_password(pw: str) -> str:
    """
    把明文密码变成带盐的哈希字符串(存进数据库的就是它)。〔Req 4.1〕
    bcrypt 每次自动生成不同的盐,所以同一个密码每次哈希结果都不同。
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pw.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    """校验明文密码是否和数据库里的哈希匹配。〔Req 2.2〕"""
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # 哈希值损坏等异常,一律视为不匹配
        return False


# ---------- 令牌 ----------

def make_token(email: str) -> str:
    """
    给登录成功的用户签发一张 JWT。〔Req 2.2 / 2.4〕
    里面装着:用户标识(sub) + 过期时间(exp)。
    """
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": email,                                   # subject = 用户标识
        "iat": now,                                     # 签发时间
        "exp": now + dt.timedelta(hours=TOKEN_TTL_HOURS),  # 过期时间
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


class AuthError(Exception):
    """令牌无效/过期时抛出,上层据此返回 401。〔Req 3.3〕"""
    pass


def parse_token(token: str) -> str:
    """
    校验令牌(签名 + 是否过期),通过则返回里面的 email。〔Req 3.1 / 3.3〕
    失败抛 AuthError,由 handler 转成 401。
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise AuthError("令牌已过期")
    except jwt.InvalidTokenError:
        raise AuthError("令牌无效")
