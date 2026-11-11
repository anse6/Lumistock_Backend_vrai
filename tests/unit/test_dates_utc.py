"""Les horodatages UTC partent avec « Z » pour être lus à l'heure locale du navigateur."""
from datetime import datetime, timezone

from utils.dates import iso_utc, utc_naif


def test_iso_utc():
    assert iso_utc(datetime(2026, 11, 10, 23, 30)) == "2026-11-10T23:30:00Z"
    assert iso_utc("2026-11-10T23:30:00.123") == "2026-11-10T23:30:00.123Z"
    assert iso_utc("2026-11-10T23:30:00Z") == "2026-11-10T23:30:00Z"           # pas de double Z
    assert iso_utc("2026-11-10T23:30:00+01:00") == "2026-11-10T23:30:00+01:00"
    assert iso_utc("2026-11-10") == "2026-11-10"                                # simple date : inchangée
    assert iso_utc(datetime(2026, 11, 10, 23, 30, tzinfo=timezone.utc)).endswith("+00:00")
    assert iso_utc(None) is None


def test_utc_naif():
    assert utc_naif("2026-11-10T23:30:00Z") == "2026-11-10T23:30:00"
    assert utc_naif("2026-11-10T23:30:00+00:00") == "2026-11-10T23:30:00"
    assert utc_naif("2026-11-10T23:30:00") == "2026-11-10T23:30:00"
