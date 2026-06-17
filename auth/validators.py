"""
validators.py —— 输入校验

这里都是"纯函数":给一个输入,返回一个判断结果,
不碰数据库、不碰网络,所以最容易测试(对应 tasks 的 T9.1)。
"""

import re

# 一个够用的邮箱格式正则:xxx@yyy.zzz
# ^ 开头, $ 结尾, 中间不允许空格
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    """邮箱格式是否合法。〔Req 1.1〕"""
    if not email or not isinstance(email, str):
        return False
    return bool(_EMAIL_RE.match(email.strip()))


def is_strong_password(pw: str) -> bool:
    """
    密码是否够强:长度 >= 8,且同时含字母和数字。〔Req 1.2 / 1.7〕
    """
    if not pw or not isinstance(pw, str):
        return False
    if len(pw) < 8:
        return False
    has_letter = any(c.isalpha() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    return has_letter and has_digit


def normalize_email(email: str) -> str:
    """
    规范化邮箱:去掉首尾空格 + 全部转小写。〔Req 4.4〕
    这样 'A@X.com ' 和 'a@x.com' 会被当成同一个邮箱,
    避免重复注册,也保证登录时查得到。
    """
    return (email or "").strip().lower()
