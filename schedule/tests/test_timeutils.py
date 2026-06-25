"""Tests de timeutils."""
from datetime import datetime

from app.core.timeutils import compute_next_daily_run, parse_hhmmss


def test_parse_hhmmss():
    t = parse_hhmmss("02:30:15")
    assert (t.hour, t.minute, t.second) == (2, 30, 15)
    t2 = parse_hhmmss("07:00")
    assert (t2.hour, t2.minute, t2.second) == (7, 0, 0)


def test_compute_next_daily_returns_future_utc():
    after = datetime(2030, 1, 1, 0, 0, 0)  # UTC naive
    nxt = compute_next_daily_run("02:00:00", "America/Mexico_City", after=after)
    assert nxt > after
    # America/Mexico_City es UTC-6: 02:00 local -> 08:00 UTC
    assert nxt.hour == 8


def test_compute_next_daily_rolls_to_next_day():
    # Si ya paso la hora local hoy, debe ir al dia siguiente.
    after = datetime(2030, 1, 1, 12, 0, 0)  # 06:00 local MX
    nxt = compute_next_daily_run("02:00:00", "America/Mexico_City", after=after)
    assert nxt.day == 2  # siguiente dia en UTC
