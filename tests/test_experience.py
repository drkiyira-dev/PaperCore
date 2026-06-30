"""体验区配额（experience.py）：三层限额 + 留邮箱 + 统计。

每个用例独立临时 DB（不碰真实 data/experience.db）；配额值按需 monkeypatch。
"""
import pytest
import experience


@pytest.fixture
def exp(tmp_path, monkeypatch):
    """开启体验区 + 指向独立临时 DB，逐用例隔离。"""
    monkeypatch.setenv("EXPERIENCE_MODE", "1")
    monkeypatch.setattr(experience, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(experience, "DB_PATH", str(tmp_path / "exp.db"))
    monkeypatch.setattr(experience, "_initialized", False)   # 强制在临时库重新建表
    return monkeypatch


def test_off_when_disabled(monkeypatch):
    monkeypatch.delenv("EXPERIENCE_MODE", raising=False)
    r = experience.check("v1", "1.2.3.4")
    assert r["can_use"] is True and r["reason"] == "off"


def test_visitor_quota(exp):
    exp.setattr(experience, "DAILY_QUOTA", 3)
    exp.setattr(experience, "IP_DAILY_CAP", 1000)
    exp.setattr(experience, "GLOBAL_DAILY_CAP", 1000)
    vid, ip = "visitorA", "10.0.0.1"
    assert experience.check(vid, ip)["can_use"] is True
    for _ in range(3):
        experience.record(vid, ip)
    r = experience.check(vid, ip)
    assert r["can_use"] is False and r["reason"] == "visitor"


def test_global_circuit_breaker(exp):
    exp.setattr(experience, "DAILY_QUOTA", 100)
    exp.setattr(experience, "IP_DAILY_CAP", 100)
    exp.setattr(experience, "GLOBAL_DAILY_CAP", 2)
    experience.record("a", "1.1.1.1")
    experience.record("b", "2.2.2.2")
    r = experience.check("c", "3.3.3.3")
    assert r["can_use"] is False and r["reason"] == "global"


def test_ip_soft_cap(exp):
    exp.setattr(experience, "DAILY_QUOTA", 100)
    exp.setattr(experience, "GLOBAL_DAILY_CAP", 1000)
    exp.setattr(experience, "IP_DAILY_CAP", 2)
    ip = "9.9.9.9"
    experience.record("u1", ip)
    experience.record("u2", ip)
    r = experience.check("u3", ip)   # 同 IP、不同 vid（模拟清 cookie 刷量）
    assert r["can_use"] is False and r["reason"] == "ip"


def test_waitlist_idempotent_and_validated(exp):
    assert experience.add_waitlist("a@b.com")[0] is True
    assert experience.add_waitlist("a@b.com")[0] is True       # 重复幂等
    ok, msg = experience.add_waitlist("not-an-email")
    assert ok is False and msg == "invalid"


def test_stats_counts(exp):
    experience.record("v", "1.1.1.1")
    experience.log_cost("v", "flash", 1000, 2000, ok=True)
    s = experience.stats()
    assert s["analyses"] == 1
    assert s["unique_visitors"] == 1
    assert s["cost_calls"] == 1
    assert s["est_cost_cny"] >= 0
