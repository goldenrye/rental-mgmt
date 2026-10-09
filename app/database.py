from collections.abc import Generator
from os import environ
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine
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


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
