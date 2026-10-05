from datetime import datetime, timezone

from signalbot.oneshot import report_due, scan_due


def t(h, m, d=5):  # 2026-10-05 — понедельник
    return datetime(2026, 10, d, h, m, tzinfo=timezone.utc)


def test_scan_due_every_4h_first_window_only():
    assert scan_due(t(0, 0), 240) and scan_due(t(4, 10), 240) and scan_due(t(8, 29), 240)
    assert not scan_due(t(4, 30), 240) and not scan_due(t(5, 0), 240) and not scan_due(t(3, 30), 240)


def test_scan_due_fires_exactly_once_per_interval():
    hits = [(h, m) for h in range(24) for m in (0, 30) if scan_due(t(h, m), 240)]
    assert hits == [(0, 0), (4, 0), (8, 0), (12, 0), (16, 0), (20, 0)]


def test_report_due_monday_9_utc_once():
    assert report_due(t(9, 0), "mon", 9) and report_due(t(9, 15), "mon", 9)
    assert not report_due(t(9, 30), "mon", 9) and not report_due(t(10, 0), "mon", 9)
    assert not report_due(t(9, 0, d=6), "mon", 9)  # вторник
