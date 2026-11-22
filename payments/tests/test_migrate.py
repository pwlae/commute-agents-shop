import os

import pytest

from app import migrate

DSN = os.environ.get("PAYMENTS_TEST_DSN")


def test_files_are_numbered_and_paired():
    ups = migrate.pending()
    assert [v for v, _ in ups] == [1, 2, 3, 4, 5]
    for _, p in ups:
        assert p.with_name(p.name.replace(".up.", ".down.")).exists()


@pytest.mark.skipif(not DSN, reason="set PAYMENTS_TEST_DSN")
def test_up_is_idempotent_and_matches_golang_migrate():
    import psycopg
    with psycopg.connect(DSN) as c:
        migrate.up(c)
        assert migrate.up(c) == 0
        assert c.execute("SELECT version, dirty FROM schema_migrations"
                         ).fetchall() == [(5, False)]
