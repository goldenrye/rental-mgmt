"""Transactional, owner-scoped conversion between plaintext and encrypted storage."""

from sqlalchemy import select, update
from sqlalchemy.orm.exc import StaleDataError

from app.encrypted_models import owner_for
from app.encryption import EncryptionError, data_key, decrypt_record, encrypt_record, master_key, unit_name_index
from app.models import ENCRYPTED_MODELS, LandlordProfile


def set_encryption_preference(db, landlord_id: int, enabled: bool) -> None:
    connection = db.connection()
    profile = LandlordProfile.__table__
    if connection.dialect.name == "sqlite":
        connection.execute(update(profile).where(profile.c.id == landlord_id)
                           .values(encryption_enabled=profile.c.encryption_enabled))
    previous = connection.scalar(select(profile.c.encryption_enabled).where(profile.c.id == landlord_id).with_for_update())
    if previous is None:
        raise EncryptionError("Landlord account is unavailable.")
    if bool(previous) == enabled:
        return
    owner = f"landlord:{landlord_id}"
    if enabled:
        master_key()
        data_key(connection, owner, create=True)
    for model in ENCRYPTED_MODELS:
        table = model.__table__
        for row in connection.execute(select(table).where(table.c.encryption_owner == owner)).mappings():
            if owner_for(connection, table.name, row, owner) != owner:
                raise EncryptionError("Record ownership does not match.")
            values = decrypt_record(connection, owner, table.name, row["id"], row["encrypted_data"], enabled=bool(previous))
            changes = {"encrypted_data": encrypt_record(connection, owner, table.name, row["id"], values, enabled=enabled)}
            if table.name == "rental_units":
                changes["name_index"] = unit_name_index(connection, owner, row["property_id"], values["name"], enabled=enabled)
            result = connection.execute(update(table).where(
                table.c.id == row["id"], table.c.encrypted_data == row["encrypted_data"],
            ).values(**changes))
            if result.rowcount != 1:
                raise StaleDataError("A record changed during conversion.")
    connection.execute(update(profile).where(profile.c.id == landlord_id).values(encryption_enabled=enabled))
