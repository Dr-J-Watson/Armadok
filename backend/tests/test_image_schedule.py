"""Planning des vérifications d'images, lien vers le paquet, détection d'une version plus haute."""
from datetime import datetime

import pytest

from app import registry, schedule


def ts(y, m, d, h=0, mi=0, se=0):
    return datetime(y, m, d, h, mi, se).timestamp()


def rules(*r):
    return schedule.validate(list(r))


def at(spec, y, m, d, h=0, mi=0, se=0):
    n = schedule.next_run(rules(spec), ts(y, m, d, h, mi, se))
    return datetime.fromtimestamp(n) if n else None


def test_every_h_m_s():
    r = rules({"kind": "every", "days": 0, "hours": 1, "minutes": 30, "seconds": 15})
    assert schedule.next_run(r, 1000.0) == 1000.0 + 5415
    assert schedule.describe(r) == ["Toutes les 1 h 30 min 15 s"]
    old = schedule.validate({"kind": "every", "every": 2, "unit": "days"})   # ancien format
    assert schedule.next_run(old, 0.0) == 172800.0


def test_daily_with_seconds():
    d = {"kind": "daily", "every": 1, "time": "16:30:45"}
    assert at(d, 2026, 11, 16, 10) == datetime(2026, 11, 16, 16, 30, 45)
    assert at(d, 2026, 11, 16, 16, 30, 45) == datetime(2026, 11, 17, 16, 30, 45)   # pile à l'heure : le lendemain
    assert at({**d, "every": 3}, 2026, 11, 16, 16, 30, 45) == datetime(2026, 11, 19, 16, 30, 45)


def test_weekly_tuesday_16h():
    s = {"kind": "weekly", "days": [1], "time": "16:00"}
    assert at(s, 2026, 11, 16, 10) == datetime(2026, 11, 17, 16, 0)
    assert at(s, 2026, 11, 17, 16, 0) == datetime(2026, 11, 24, 16, 0)


def test_monthly_several_days_and_last():
    m = {"kind": "monthly", "days": [10, 25, "last"], "months": [], "time": "09:00:00"}
    assert at(m, 2026, 11, 11) == datetime(2026, 11, 25, 9, 0)
    assert at(m, 2026, 11, 26) == datetime(2026, 11, 30, 9, 0)
    assert at({**m, "days": [31]}, 2027, 2, 1) == datetime(2027, 2, 28, 9, 0)   # mois court : son dernier jour
    assert at({**m, "days": [1], "months": [1, 7]}, 2026, 2, 1) == datetime(2026, 7, 1, 9, 0)


def test_nth_weekday_of_month():
    first_mon = {"kind": "nth", "nth": 1, "weekday": 0, "months": [], "time": "08:00"}
    assert at(first_mon, 2026, 11, 1) == datetime(2026, 11, 2, 8, 0)
    assert at(first_mon, 2026, 11, 3) == datetime(2026, 12, 7, 8, 0)
    last_fri = {**first_mon, "nth": -1, "weekday": 4}
    assert at(last_fri, 2026, 11, 1) == datetime(2026, 11, 27, 8, 0)


def test_once_is_spent():
    o = {"kind": "once", "at": "2026-12-25T09:00"}
    assert at(o, 2026, 11, 1) == datetime(2026, 12, 25, 9, 0)
    assert at(o, 2026, 12, 25, 9, 0) is None


def test_cron_with_seconds_and_dom_dow():
    c = {"kind": "cron", "expr": "30 */15 8-18 * * 1-5"}   # secondes minutes heures jour mois jour-semaine
    assert at(c, 2026, 11, 16, 8, 0, 0) == datetime(2026, 11, 16, 8, 0, 30)
    assert at(c, 2026, 11, 20, 18, 45, 30) == datetime(2026, 11, 23, 8, 0, 30)   # vendredi soir -> lundi
    assert at({"kind": "cron", "expr": "0 12 L * *"}, 2026, 2, 1) == datetime(2026, 2, 28, 12, 0)
    assert at({"kind": "cron", "expr": "0 9 1,15 * *"}, 2026, 11, 2) == datetime(2026, 11, 15, 9, 0)


def test_several_rules_take_the_nearest():
    r = rules({"kind": "weekly", "days": [1], "time": "16:00"}, {"kind": "monthly", "days": [18], "months": [], "time": "07:00"})
    assert datetime.fromtimestamp(schedule.next_run(r, ts(2026, 11, 16, 10))) == datetime(2026, 11, 17, 16, 0)
    assert [datetime.fromtimestamp(t) for t in schedule.upcoming(r, ts(2026, 11, 16, 10), 3)] == [datetime(2026, 11, 17, 16), datetime(2026, 11, 18, 7), datetime(2026, 11, 24, 16)]


@pytest.mark.parametrize("bad", [None, {}, [], [{"kind": "every", "days": 0, "hours": 0, "minutes": 0, "seconds": 30}], [{"kind": "every", "days": 91}], [{"kind": "every", "unit": "weeks", "every": 5}],
                                 [{"kind": "weekly", "days": [], "time": "10:00"}], [{"kind": "weekly", "days": [9], "time": "10:00"}], [{"kind": "weekly", "days": [1], "time": "25:00"}],
                                 [{"kind": "monthly", "days": [32], "time": "10:00"}], [{"kind": "monthly", "days": [], "time": "10:00"}], [{"kind": "nth", "nth": 5, "weekday": 0, "time": "10:00"}],
                                 [{"kind": "once", "at": "pas une date"}], [{"kind": "cron", "expr": "* * * * * *"}], [{"kind": "cron", "expr": "61 * * * *"}], [{"kind": "cron", "expr": "a b c"}],
                                 [{"kind": "cron", "expr": "0 0 31 2 *"}], [{"kind": "daily", "every": 0, "time": "10:00"}], [{"kind": "bizarre"}], [{"kind": "daily", "every": 1, "time": "10:00"}] * 11])
def test_invalid_schedules(bad):
    with pytest.raises(ValueError):
        schedule.validate(bad)


def test_describe():
    d = schedule.describe(rules({"kind": "weekly", "days": [1, 3], "time": "16:00"}, {"kind": "monthly", "days": [1, "last"], "months": [], "time": "09:30"},
                                {"kind": "nth", "nth": -1, "weekday": 4, "months": [], "time": "08:00:05"}, {"kind": "daily", "every": 1, "time": "03:00"}))
    assert d == ["Chaque mardi et jeudi à 16 h", "Le 1er et dernier jour de chaque mois à 9 h 30", "Le dernier vendredi de chaque mois à 8 h 00 min 05 s", "Chaque jour à 3 h"]


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


def test_image_variables_are_read_from_dotenv(tmp_path):
    from app import project_ops
    (tmp_path / ".env").write_text("VERSION=1.27\nTAG='2.0'\nEMPTY=\n")
    (tmp_path / "docker-compose.yml").write_text("services:\n  a:\n    image: nginx:${VERSION}\n  b:\n    image: ghcr.io/o/b:${TAG:-latest}\n  c:\n    image: x:${NOPE}\n  d:\n    image: y:${EMPTY:-dflt}\n  e:\n    image: z:${MISSING-fallback}\n")
    sv = {s["name"]: s for s in project_ops.entry(tmp_path)["services"]}
    assert (sv["a"]["image"], sv["a"]["imageRaw"]) == ("nginx:1.27", "nginx:${VERSION}")
    assert sv["b"]["image"] == "ghcr.io/o/b:2.0"
    assert sv["c"]["image"] == "x:${NOPE}" and "imageRaw" not in sv["c"]   # inconnue : laissée telle quelle
    assert sv["d"]["image"] == "y:dflt" and sv["e"]["image"] == "z:fallback"
    assert project_ops.interpolate("a$$b", {}) == "a$b"
