"""
store.py —— DynamoDB 读写封装

把"操作数据库"的细节集中在这里,业务代码(handlers)只调用这几个函数,
不直接碰 boto3。这样以后换存储、改表结构,只动这一个文件。

表 Users:
- 主键: email (字符串, 已规范化为小写)
- 字段: passwordHash, createdAt
"""

import os
import datetime as dt

import boto3
from botocore.exceptions import ClientError

# 表名从环境变量读,SAM 模板里配置〔Req T4.3 / §6〕
TABLE_NAME = os.environ.get("TABLE_NAME", "Users")

# boto3 的资源句柄。Lambda 里复用同一个连接,放在模块级别(函数外)。
_dynamodb = boto3.resource("dynamodb")


def _table():
    """延迟获取表对象(方便测试时替换连接)。"""
    return _dynamodb.Table(TABLE_NAME)


class EmailExists(Exception):
    """邮箱已被注册时抛出,上层转成 409。〔Req 1.5〕"""
    pass


def get_user(email: str):
    """
    按邮箱读取用户。找到返回 dict,找不到返回 None。〔Req 2.1 / 3.1〕
    传入的 email 应已由 validators.normalize_email 规范化过。
    """
    resp = _table().get_item(Key={"email": email})
    return resp.get("Item")  # 没有 Item 时返回 None


def create_user(email: str, password_hash: str) -> dict:
    """
    创建新用户。〔Req 1.3 / 1.5 / 4.2〕
    用 ConditionExpression 保证 email 不存在才写入 ——
    从数据库层面挡住"重复注册",即使两个请求同时进来也只会成功一个。
    """
    item = {
        "email": email,
        "passwordHash": password_hash,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    try:
        _table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(email)",
        )
    except ClientError as e:
        # 条件不满足 = 邮箱已存在
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise EmailExists(email)
        raise  # 其它错误原样抛出
    return item


def public_view(user: dict) -> dict:
    """
    把用户对象转成"能返回给前端"的样子:去掉密码哈希。〔Req 1.8 / 4.3〕
    """
    return {
        "email": user["email"],
        "createdAt": user.get("createdAt"),
    }
