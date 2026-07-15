from django.conf import settings


def test_sqlite_waits_for_concurrent_writers():
    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.sqlite3":
        return

    assert database["OPTIONS"]["timeout"] >= 30
