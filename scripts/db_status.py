import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, database_url
from app.models import Lease, RentalBill, RentalProperty, RentalUnit, TenantProfile, User


def count_rows(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def main() -> None:
    with SessionLocal() as session:
        print(f"database_url={database_url}")
        print(f"users={count_rows(session, User)}")
        print(f"properties={count_rows(session, RentalProperty)}")
        print(f"units={count_rows(session, RentalUnit)}")
        print(f"tenants={count_rows(session, TenantProfile)}")
        print(f"leases={count_rows(session, Lease)}")
        print(f"bills={count_rows(session, RentalBill)}")


if __name__ == "__main__":
    main()
