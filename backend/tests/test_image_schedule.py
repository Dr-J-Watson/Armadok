"""Planning des vérifications d'images, lien vers le paquet, détection d'une version plus haute."""
from datetime import datetime

import pytest

from app import registry, schedule


def ts(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi).timestamp()


def test_every():
    s = schedule.validate({"kind": "every", "every": "6", "unit": "hours"})
    assert schedule.next_run(s, 1000.0) == 1000.0 + 6 * 3600
    assert schedule.next_run(schedule.validate({"kind": "every", "every": 2, "unit": "days"}), 0.0) == 172800.0


def test_weekly_tuesday_16h():
    s = schedule.validate({"kind": "weekly", "days": [1], "time": "16:00"})
    mon = ts(2026, 11, 16, 10)   # un lundi
    assert datetime.fromtimestamp(schedule.next_run(s, mon)) == datetime(2026, 11, 17, 16, 0)
    tue_after = ts(2026, 11, 17, 16, 0)   # pile à l'heure : la suivante est la semaine d'après
    assert datetime.fromtimestamp(schedule.next_run(s, tue_after)) == datetime(2026, 11, 24, 16, 0)


def test_monthly_clamps_short_months():
    s = schedule.validate({"kind": "monthly", "day": 31, "time": "09:30"})
    assert datetime.fromtimestamp(schedule.next_run(s, ts(2027, 2, 1))) == datetime(2027, 2, 28, 9, 30)
    s10 = schedule.validate({"kind": "monthly", "day": 10, "time": "09:00"})
    assert datetime.fromtimestamp(schedule.next_run(s10, ts(2026, 12, 10, 9, 0))) == datetime(2027, 1, 10, 9, 0)


@pytest.mark.parametrize("bad", [None, {}, {"kind": "every", "every": 0, "unit": "hours"}, {"kind": "every", "every": 5, "unit": "weeks"}, {"kind": "weekly", "days": [], "time": "10:00"},
                                 {"kind": "weekly", "days": [9], "time": "10:00"}, {"kind": "weekly", "days": [1], "time": "25:00"}, {"kind": "monthly", "day": 32, "time": "10:00"}])
def test_invalid_schedules(bad):
    with pytest.raises(ValueError):
        schedule.validate(bad)


def test_describe():
    assert schedule.describe({"kind": "weekly", "days": [1], "time": "16:00"}) == "Chaque mardi à 16 h"
    assert schedule.describe({"kind": "monthly", "day": 10, "time": "09:30"}) == "Le 10 de chaque mois à 09 h 30"
    assert schedule.describe({"kind": "every", "every": 1, "unit": "days"}) == "Chaque jour"


def test_package_urls():
    p = registry.parse_ref
    assert registry.package_url(p("ghcr.io/dr-j-watson/armadok:latest")).startswith("https://github.com/dr-j-watson?tab=packages")
    assert registry.package_url(p("nginx:1.27")) == "https://hub.docker.com/_/nginx"
    assert registry.package_url(p("gelbphoenix/autocaliweb")) == "https://hub.docker.com/r/gelbphoenix/autocaliweb"
    assert registry.package_url(p("registry.example.org/x/y")) is None


def test_newer_version_same_shape_only():
    tags = ["1.26", "1.27", "1.28", "1.28-alpine", "1.29.1", "2.0-rc1", "latest", "2.0", "1.30-alpine"]
    assert registry.newer_version("1.27", tags) == "2.0"
    assert registry.newer_version("1.27-alpine", tags) == "1.30-alpine"
    assert registry.newer_version("1.27.0", tags) == "1.29.1"
    assert registry.newer_version("latest", tags) is None
    assert registry.newer_version("2.0", tags) is None
    assert registry.newer_version("v1.4.1", ["v1.4.1", "v1.5.0", "1.9.0"]) == "v1.5.0"
