from collections.abc import Generator
from os import environ
from pathlib import Path
import re
import sqlite3

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "rental_mgmt.db"
LEGACY_SQLITE_PATH = PROJECT_ROOT / "rental_mgmt.db"
DATA_TABLES = ("users", "rental_properties", "rental_units", "tenant_profiles", "leases", "rental_bills")


def sqlite_record_count(database_path: Path) -> int:
    if not database_path.exists():
        return 0
    try:
        with sqlite3.connect(database_path) as connection:
            total = 0
            for table in DATA_TABLES:
                try:
                    total += connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.Error:
                    continue
            return total
    except sqlite3.Error:
        return 0


def migrate_legacy_sqlite_file(database_path: Path) -> None:
    if database_path != DEFAULT_SQLITE_PATH:
        return
    if not LEGACY_SQLITE_PATH.exists():
        return
    if database_path.exists() and sqlite_record_count(database_path) > 0:
        return
    if sqlite_record_count(LEGACY_SQLITE_PATH) == 0:
        return

    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()
    LEGACY_SQLITE_PATH.replace(database_path)


def resolve_database_url(database_url: str) -> str:
    if not database_url.startswith("sqlite:///") or database_url == "sqlite:///:memory:":
        return database_url

    sqlite_path = database_url.removeprefix("sqlite:///")
    if sqlite_path.startswith("/"):
        database_path = Path(sqlite_path)
    else:
        database_path = PROJECT_ROOT / sqlite_path

    database_path.parent.mkdir(parents=True, exist_ok=True)
    migrate_legacy_sqlite_file(database_path)
    return f"sqlite:///{database_path}"


database_url = resolve_database_url(settings.database_url)
environ["RENTAL_MGMT_RESOLVED_DATABASE_URL"] = database_url
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}

engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def migrate_legacy_bill_uniqueness(bind: Engine) -> bool:
    """Remove the obsolete SQLite constraint without changing bills or payment links."""
    if bind.dialect.name != "sqlite":
        return False
    connection = bind.raw_connection()
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    try:
        # SQLite table rebuilds must disable FK actions before starting the transaction.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='rental_bills'"
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        ddl, removed = re.subn(
            r',\s*CONSTRAINT\s+"?uq_bill_month"?\s+UNIQUE\s*'
            r'\(\s*lease_id\s*,\s*tenant_id\s*,\s*bill_year\s*,\s*bill_month\s*\)',
            "", row[0], flags=re.IGNORECASE,
        )
        if removed == 0:
            connection.rollback()
            return False
        before_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        schema_objects = connection.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='rental_bills' "
            "AND type IN ('index', 'trigger') AND sql IS NOT NULL"
        ).fetchall()
        ddl, renamed = re.subn(
            r'^(CREATE TABLE\s+)"?rental_bills"?',
            r'\1rental_bills_migrating', ddl, count=1, flags=re.IGNORECASE,
        )
        if renamed != 1:
            raise RuntimeError("Unrecognized legacy rental_bills schema")
        connection.execute(ddl)
        connection.execute("INSERT INTO rental_bills_migrating SELECT * FROM rental_bills")
        connection.execute("DROP TABLE rental_bills")
        connection.execute("ALTER TABLE rental_bills_migrating RENAME TO rental_bills")
        for (sql,) in schema_objects:
            connection.execute(sql)
        if connection.execute("PRAGMA foreign_key_check").fetchall() != before_violations:
            raise RuntimeError("Bill migration changed foreign key integrity")
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys={int(foreign_keys)}")
        connection.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
