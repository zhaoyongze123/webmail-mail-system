"""Redis 客户端单例与连接入口。"""

from functools import lru_cache

import redis

from app.config import get_settings


@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis:
    """创建或复用当前进程的 Redis 连接。"""
    return redis.Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
    )
