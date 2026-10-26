from sqlalchemy import text

CREATE = "CREATE TABLE IF NOT EXISTS scratch (id int)"


def test_a_row_can_be_written_and_read(db):
    db.execute(text(CREATE))
    db.execute(text("INSERT INTO scratch VALUES (1)"))
    db.commit()
    assert db.execute(text("SELECT count(*) FROM scratch")).scalar_one() == 1


def test_rows_from_the_previous_test_are_gone(db):
    db.execute(text(CREATE))
    db.commit()
    assert db.execute(text("SELECT count(*) FROM scratch")).scalar_one() == 0
