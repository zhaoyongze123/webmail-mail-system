import fakeredis

from app.cache import JsonCache, LoginFailureLimiter, RedisLock, SessionStore
from app.config import Settings


def make_settings() -> Settings:
    return Settings(
        SESSION_TTL_SECONDS=60,
        LOGIN_FAIL_TTL_SECONDS=30,
        LOGIN_FAIL_LIMIT=3,
    )


def make_client():
    return fakeredis.FakeRedis(decode_responses=True)


def test_session_store_create_get_refresh_delete() -> None:
    client = make_client()
    store = SessionStore(client=client, settings=make_settings())

    session_id = store.create({"email": "test@mdaemon.cc", "roles": ["user"]}, session_id="sid")

    assert session_id == "sid"
    assert store.get("sid") == {"email": "test@mdaemon.cc", "roles": ["user"]}
    assert client.ttl("session:sid") > 0
    assert store.refresh("sid") is True
    assert store.delete("sid") is True
    assert store.get("sid") is None


def test_login_failure_limiter_records_ttl_and_clear() -> None:
    client = make_client()
    limiter = LoginFailureLimiter(client=client, settings=make_settings())

    assert limiter.is_limited("127.0.0.1", "Test@Mdaemon.cc") is False
    assert limiter.record_failure("127.0.0.1", "Test@Mdaemon.cc") == 1
    assert limiter.record_failure("127.0.0.1", "test@mdaemon.cc") == 2
    assert limiter.record_failure("127.0.0.1", "test@mdaemon.cc") == 3
    assert limiter.is_limited("127.0.0.1", "test@mdaemon.cc") is True
    assert client.ttl("login_fail:127.0.0.1:test@mdaemon.cc") > 0

    limiter.clear("127.0.0.1", "test@mdaemon.cc")
    assert limiter.is_limited("127.0.0.1", "test@mdaemon.cc") is False


def test_json_cache_roundtrip_and_delete() -> None:
    cache = JsonCache(client=make_client())

    cache.set("folder_cache:account", {"folders": ["INBOX", ".Sent"]}, ttl_seconds=60)

    assert cache.get("folder_cache:account") == {"folders": ["INBOX", ".Sent"]}
    assert cache.delete("folder_cache:account") is True
    assert cache.get("folder_cache:account") is None


def test_redis_lock_acquires_exclusively_and_releases() -> None:
    lock = RedisLock(client=make_client())

    with lock.acquire("mail_lock:account:INBOX", ttl_seconds=60, token="one") as acquired:
        assert acquired is True
        with lock.acquire("mail_lock:account:INBOX", ttl_seconds=60, token="two") as second:
            assert second is False

    with lock.acquire("mail_lock:account:INBOX", ttl_seconds=60, token="three") as acquired_again:
        assert acquired_again is True
