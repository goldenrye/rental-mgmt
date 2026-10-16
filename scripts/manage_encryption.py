"""Offline encryption administration; never prints encryption keys or landlord data."""

import argparse
import base64
import os
from pathlib import Path


def create_key_file(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(base64.urlsafe_b64encode(os.urandom(32)).decode() + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init-key", "migrate", "verify"])
    parser.add_argument("--confirm-backup", action="store_true",
                        help="Confirm a database backup and a separate key backup exist, and API/MCP are stopped")
    args = parser.parse_args()
    from app.config import get_settings
    if args.action == "init-key":
        path = Path(get_settings().data_encryption_key_file)
        try:
            create_key_file(path)
        except FileExistsError:
            parser.exit(1, "Key file already exists; left unchanged. Never replace a key protecting existing data.\n")
        print("Created a private encryption key file. Back it up separately from the database.")
        return
    if args.action == "migrate" and not args.confirm_backup:
        parser.error("Stop API/MCP, back up the database and key separately, then supply --confirm-backup.")
    from app.database import engine
    from app.encryption_migration import migrate_database, verify_database
    try:
        counts = migrate_database(engine) if args.action == "migrate" else verify_database(engine)
        for table, count in counts.items():
            print(f"{table}: {count} records")
        print("Completed. Old database files, WAL files, snapshots, and backups may still contain plaintext.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
