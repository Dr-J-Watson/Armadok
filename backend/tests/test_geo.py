from app import geo


class FakeReader:
    def get(self, ip):
        return {"country": {"iso_code": "FR", "names": {"fr": "France", "en": "France"}}, "city": {"names": {"en": "Paris"}}, "location": {"latitude": 48.8566, "longitude": 2.3522}} if ip == "93.22.23.51" else None


def test_lookup(monkeypatch):
    monkeypatch.setattr(geo, "_open", lambda: FakeReader())
    g = geo.lookup("93.22.23.51")
    assert g["country"] == "FR" and g["city"] == "Paris" and g["lat"] == 48.86
    assert "lat" not in geo.lookup("93.22.23.51", precise=False) and "city" not in geo.lookup("93.22.23.51", precise=False)
    assert geo.lookup("192.168.1.5") is None and geo.lookup("pas-une-ip") is None and geo.lookup("8.8.8.8") is None


def test_no_database(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOIP_DB", str(tmp_path / "absent.mmdb"))
    assert geo.lookup("93.22.23.51") is None and geo.status()["available"] is False
