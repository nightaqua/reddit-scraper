import threading
from datetime import datetime, timezone, timedelta

from security import RateLimiter


def test_initial_state():
    limiter = RateLimiter()
    assert not limiter.is_locked()
    assert limiter.seconds_remaining() == 0
    assert limiter.failed_attempts() == 0


def test_failure_increments_counter():
    limiter = RateLimiter()
    limiter.record_failure()
    limiter.record_failure()
    assert limiter.failed_attempts() == 2
    assert not limiter.is_locked()


def test_lockout_does_not_trigger_before_threshold():
    limiter = RateLimiter()
    for _ in range(RateLimiter.LOCKOUT_THRESHOLD - 1):
        limiter.record_failure()
    assert not limiter.is_locked()


def test_lockout_triggers_at_threshold():
    limiter = RateLimiter()
    for _ in range(RateLimiter.LOCKOUT_THRESHOLD):
        limiter.record_failure()
    assert limiter.is_locked()
    assert limiter.seconds_remaining() > 0


def test_success_resets_state():
    limiter = RateLimiter()
    for _ in range(RateLimiter.LOCKOUT_THRESHOLD):
        limiter.record_failure()
    assert limiter.is_locked()
    limiter.record_success()
    assert not limiter.is_locked()
    assert limiter.failed_attempts() == 0
    assert limiter.seconds_remaining() == 0


def test_lockout_auto_clears_after_expiry():
    limiter = RateLimiter()
    for _ in range(RateLimiter.LOCKOUT_THRESHOLD):
        limiter.record_failure()
    # Manually backdate the lockout to simulate expiry
    limiter._locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert not limiter.is_locked()
    assert limiter.seconds_remaining() == 0


def test_concurrent_failures_no_corruption():
    limiter = RateLimiter()
    threads = [threading.Thread(target=limiter.record_failure) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert limiter.failed_attempts() == 20
