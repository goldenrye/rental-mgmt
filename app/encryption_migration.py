"""Offline, transactional conversion from plaintext rows to encrypted payloads."""

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import MetaData, Table, UniqueConstraint, inspect, select, text
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base
from app.encrypted_models import owner_for
from app.encryption import EncryptionError, data_key, decrypt_record, encrypt_record, encryption_enabled, key_table, unit_name_index
from app.models import ENCRYPTED_MODELS, LandlordProfile


def ensure_preference_column(connection):
    inspector = inspect(connection)
    if not inspector.has_table("landlord_profiles"):
        return
    if "encryption_enabled" in {column["name"] for column in inspector.get_columns("landlord_profiles")}:
        return
    connection.exec_driver_sql("ALTER TABLE landlord_profiles ADD COLUMN encryption_enabled BOOLEAN NOT NULL DEFAULT FALSE")
    # The previous release encrypted all records. Never silently downgrade those accounts.
    for model in ENCRYPTED_MODELS:
        name = model.__tablename__
        if inspector.has_table(name) and "encrypted_data" in {column["name"] for column in inspector.get_columns(name)}:
            for owner in connection.scalars(text(
                f'SELECT DISTINCT encryption_owner FROM "{name}" WHERE encrypted_data LIKE \'v1:%\''
            )):
                if owner.startswith("landlord:"):
                    connection.execute(LandlordProfile.__table__.update().where(
                        LandlordProfile.id == int(owner.split(":")[1]),
                    ).values(encryption_enabled=True))


def initialize_encrypted_database(bind):
    inspector = inspect(bind)
    names = set(inspector.get_table_names())
    for model in ENCRYPTED_MODELS:
        if model.__tablename__ in names:
            columns = {column["name"] for column in inspector.get_columns(model.__tablename__)}
            if set(model.__sensitive_fields__) & columns or not {"encrypted_data", "encryption_owner"} <= columns:
                raise EncryptionError(
                    "Legacy schema detected. Stop API/MCP and back up the database, "
                    "then run python -m scripts.manage_encryption migrate --confirm-backup."
                )
    Base.metadata.create_all(bind)
    with bind.begin() as connection:
        ensure_preference_column(connection)
        for model in ENCRYPTED_MODELS:
            for owner in connection.scalars(select(model.__table__.c.encryption_owner).distinct()):
                if encryption_enabled(connection, owner):
                    data_key(connection, owner)


def encrypted_row(connection, model, row):
    values = {}
    for name, field in model.__sensitive_fields__.items():
        value = row.get(name, field.default)
        if value is None and field.required:
            raise EncryptionError(f"Cannot migrate missing required field {model.__tablename__}.{name}.")
        if value is not None and field.money:
            value = str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        values[name] = value
    owner = owner_for(connection, model.__tablename__, row)
    payload = encrypt_record(connection, owner, model.__tablename__, row["id"], values)
    if decrypt_record(connection, owner, model.__tablename__, row["id"], payload) != values:
        raise EncryptionError("Migration verification failed.")
    result = {name: value for name, value in row.items() if name not in model.__sensitive_fields__}
    result.update(encryption_owner=owner, encrypted_data=payload)
    if model.__tablename__ == "rental_units":
        result["name_index"] = unit_name_index(connection, owner, row["property_id"], row["name"])
    return result


def migrate_database(bind):
    """Run while writers are stopped. Preserve plaintext unless the landlord opted in."""
    if bind.dialect.name not in {"sqlite", "postgresql"}:
        raise EncryptionError("Migration supports SQLite and PostgreSQL only.")
    counts = {}
    with bind.connect() as connection:
        sqlite = bind.dialect.name == "sqlite"
        foreign_keys = None
        try:
            if sqlite:
                foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.exec_driver_sql("PRAGMA secure_delete=ON")
                connection.commit()
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                original_violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            else:
                connection.begin()
                tables = set(inspect(connection).get_table_names())
                locked = [model.__tablename__ for model in ENCRYPTED_MODELS if model.__tablename__ in tables]
                if locked:
                    connection.exec_driver_sql("LOCK TABLE " + ", ".join(locked) + " IN ACCESS EXCLUSIVE MODE")
            key_table.create(connection, checkfirst=True)
            ensure_preference_column(connection)
            names = set(inspect(connection).get_table_names())
            for model in ENCRYPTED_MODELS:
                name = model.__tablename__
                if name not in names:
                    continue
                old = Table(name, MetaData(), autoload_with=connection)
                columns = set(old.c.keys())
                sensitive = set(model.__sensitive_fields__)
                if "encrypted_data" in columns:
                    if sensitive & columns:
                        raise EncryptionError("Mixed plaintext/encrypted schema detected; restore a consistent backup.")
                    continue
                retained = set(model.__table__.c.keys()) - {"encrypted_data", "encryption_owner", "name_index"}
                if not retained <= columns or columns - retained - sensitive:
                    raise EncryptionError(f"Unsupported schema for {name}; upgrade the plaintext schema first.")
                rows = connection.execute(select(old)).mappings()
                count = 0
                if sqlite:
                    triggers = connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (name,),
                    ).all()
                    if triggers:
                        raise EncryptionError(f"Custom triggers on {name} require manual migration.")
                    indexes = inspect(connection).get_indexes(name)
                    known_indexes = {index.name for index in model.__table__.indexes} | {"ix_tenant_profiles_email"}
                    if any(index["name"] not in known_indexes for index in indexes):
                        raise EncryptionError(f"Custom indexes on {name} require manual migration.")
                    known_constraints = {
                        tuple(column.name for column in constraint.columns)
                        for constraint in model.__table__.constraints if isinstance(constraint, UniqueConstraint)
                    }
                    if name == "rental_units":
                        known_constraints.add(("property_id", "name"))
                    if name == "rental_bills":
                        known_constraints.add(("lease_id", "tenant_id", "bill_year", "bill_month"))
                    if any(tuple(item["column_names"]) not in known_constraints
                           for item in inspect(connection).get_unique_constraints(name)):
                        raise EncryptionError(f"Custom uniqueness constraints on {name} require manual migration.")
                    metadata = MetaData()
                    for table in Base.metadata.tables.values():
                        table.to_metadata(metadata)
                    replacement = model.__table__.to_metadata(metadata, name=f"encrypting_{name}")
                    connection.execute(CreateTable(replacement))
                    for row in rows:
                        connection.execute(replacement.insert().values(**encrypted_row(connection, model, row)))
                        count += 1
                    connection.exec_driver_sql(f'DROP TABLE "{name}"')
                    connection.exec_driver_sql(f'ALTER TABLE "encrypting_{name}" RENAME TO "{name}"')
                    for index in model.__table__.indexes:
                        connection.execute(CreateIndex(index))
                else:
                    connection.exec_driver_sql(f'ALTER TABLE "{name}" ADD COLUMN encrypted_data TEXT, ADD COLUMN encryption_owner VARCHAR(128)')
                    if name == "rental_units":
                        connection.exec_driver_sql(f'ALTER TABLE "{name}" ADD COLUMN name_index VARCHAR(64)')
                    for row in rows:
                        values = encrypted_row(connection, model, row)
                        connection.execute(text(f'UPDATE "{name}" SET encrypted_data=:encrypted_data, encryption_owner=:encryption_owner'
                                                + (", name_index=:name_index" if name == "rental_units" else "")
                                                + " WHERE id=:id"), values)
                        count += 1
                    for field in sensitive & columns:
                        connection.exec_driver_sql(f'ALTER TABLE "{name}" DROP COLUMN "{field}"')
                    connection.exec_driver_sql(f'ALTER TABLE "{name}" ALTER COLUMN encrypted_data SET NOT NULL, ALTER COLUMN encryption_owner SET NOT NULL')
                    if name == "rental_units":
                        connection.exec_driver_sql(
                            'ALTER TABLE rental_units ALTER COLUMN name_index SET NOT NULL, '
                            'ADD CONSTRAINT uq_property_unit_name UNIQUE (property_id, name_index)'
                        )
                counts[name] = count
            Base.metadata.create_all(connection)
            if sqlite and connection.exec_driver_sql("PRAGMA foreign_key_check").all() != original_violations:
                raise EncryptionError("Migration changed foreign key integrity.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if sqlite and foreign_keys is not None:
                connection.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")
                connection.commit()
    if bind.dialect.name == "sqlite":
        # Reclaim old plaintext pages after the transactional schema/data conversion.
        with bind.connect() as connection:
            busy = connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").first()[0]
            connection.commit()
            if busy:
                raise EncryptionError("Migration committed, but WAL cleanup is blocked by a reader. Stop all readers and compact the database.")
            connection.exec_driver_sql("VACUUM")
            connection.commit()
    return counts


def verify_database(bind):
    initialize_encrypted_database(bind)
    counts = {}
    with bind.connect() as connection:
        for model in ENCRYPTED_MODELS:
            count = 0
            for row in connection.execute(select(model.__table__)).mappings():
                owner = owner_for(connection, model.__tablename__, row, row["encryption_owner"])
                if owner != row["encryption_owner"]:
                    raise EncryptionError("Encrypted record ownership does not match.")
                decrypt_record(connection, owner, model.__tablename__, row["id"], row["encrypted_data"])
                count += 1
            counts[model.__tablename__] = count
    return counts
