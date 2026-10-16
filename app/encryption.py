"""Versioned AES-GCM envelope encryption. Keys never live unwrapped in the database."""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Column, MetaData, String, Table, Text, inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import get_settings


key_table = Table(
    "landlord_encryption_keys", MetaData(),
    Column("owner", String(128), primary_key=True),
    Column("wrapped_key", Text, nullable=False),
)


class EncryptionError(RuntimeError):
    pass


def encryption_enabled(connection, owner: str, *, lock=False) -> bool:
    if not owner.startswith("landlord:"):
        # Preserve previously encrypted unassigned profiles; new ones default to plaintext.
        return connection.scalar(select(key_table.c.owner).where(key_table.c.owner == owner)) is not None
    from app.database import Base
    table = Base.metadata.tables["landlord_profiles"]
    identifier = int(owner.split(":")[1])
    if lock and connection.dialect.name == "sqlite":
        connection.execute(update(table).where(table.c.id == identifier)
                           .values(encryption_enabled=table.c.encryption_enabled))
    query = select(table.c.encryption_enabled).where(table.c.id == identifier)
    if lock:
        query = query.with_for_update(read=True)
    value = connection.scalar(query)
    if value is None:
        raise EncryptionError("Landlord storage preference is unavailable.")
    return bool(value)


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def decode(value: str) -> bytes:
    return base64.b64decode(value, altchars=b"-_", validate=True)


def master_key() -> bytes:
    settings = get_settings()
    value = settings.data_encryption_master_key.get_secret_value()
    if not value:
        try:
            value = Path(settings.data_encryption_key_file).read_text().strip()
        except OSError:
            raise EncryptionError(
                "Data encryption key unavailable. Set DATA_ENCRYPTION_MASTER_KEY or "
                "DATA_ENCRYPTION_KEY_FILE; never generate a replacement for an existing database."
            ) from None
    try:
        key = decode(value)
    except (ValueError, TypeError):
        raise EncryptionError("The data encryption key must be base64-encoded 32 random bytes.") from None
    if len(key) != 32:
        raise EncryptionError("The data encryption key must be base64-encoded 32 random bytes.")
    return key


def seal(key: bytes, plaintext: bytes, aad: str) -> str:
    nonce = os.urandom(12)
    return "v1:" + encode(nonce + AESGCM(key).encrypt(nonce, plaintext, aad.encode()))


def unseal(key: bytes, value: str, aad: str) -> bytes:
    try:
        if not value.startswith("v1:"):
            raise ValueError("Unknown format")
        data = decode(value[3:])
        return AESGCM(key).decrypt(data[:12], data[12:], aad.encode())
    except (InvalidTag, ValueError, TypeError):
        raise EncryptionError("Encrypted data could not be authenticated; check the key and database integrity.") from None


def data_key(connection, owner: str, *, create: bool = False) -> bytes:
    wrapped = connection.scalar(select(key_table.c.wrapped_key).where(key_table.c.owner == owner))
    root = master_key()
    if wrapped is None and create:
        inspector = inspect(connection)
        for table in ("tenant_profiles", "rental_properties", "rental_units", "leases",
                      "lease_tenants", "property_bills", "rental_bills", "payments"):
            if inspector.has_table(table) and "encryption_owner" in {
                column["name"] for column in inspector.get_columns(table)
            }:
                if connection.scalar(text(
                    f'SELECT 1 FROM "{table}" WHERE encryption_owner=:owner '
                    "AND encrypted_data LIKE 'v1:%' LIMIT 1"
                ), {"owner": owner}):
                    raise EncryptionError("An existing landlord key is missing; restore it, do not replace it.")
        candidate = seal(root, AESGCM.generate_key(bit_length=256), f"rental:key:{owner}")
        dialect = connection.dialect.name
        insert = {"sqlite": sqlite_insert, "postgresql": pg_insert}.get(dialect)
        if insert is None:
            raise EncryptionError("Encryption supports SQLite and PostgreSQL only.")
        connection.execute(insert(key_table).values(owner=owner, wrapped_key=candidate)
                           .on_conflict_do_nothing(index_elements=["owner"]))
        wrapped = connection.scalar(select(key_table.c.wrapped_key).where(key_table.c.owner == owner))
    if wrapped is None:
        raise EncryptionError("The landlord encryption key is missing; restore it from backup.")
    return unseal(root, wrapped, f"rental:key:{owner}")


def encrypt_record(connection, owner: str, table: str, record_id: int, values: dict, *, enabled=None) -> str:
    content = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if enabled is None:
        enabled = encryption_enabled(connection, owner, lock=True)
    if not enabled:
        return "plain:v1:" + content.decode()
    return seal(data_key(connection, owner, create=True), content, f"rental:row:{table}:{record_id}:{owner}")


def decrypt_record(connection, owner: str, table: str, record_id: int, value: str, *, enabled=None) -> dict:
    try:
        if enabled is None:
            enabled = encryption_enabled(connection, owner)
        if value.startswith("plain:v1:"):
            if enabled:
                raise EncryptionError("Plaintext was found in an encryption-enabled account.")
            content = value[len("plain:v1:"):]
        else:
            if not enabled:
                raise EncryptionError("Encrypted data does not match the account storage preference.")
            content = unseal(data_key(connection, owner), value, f"rental:row:{table}:{record_id}:{owner}")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Invalid record")
        return result
    except (ValueError, UnicodeError):
        raise EncryptionError("Invalid encrypted record.") from None


def unit_name_index(connection, owner: str, property_id: int, name: str, *, enabled=None) -> str:
    # Keyed lookup preserves per-property uniqueness without exposing the name.
    message = json.dumps(["unit-name", property_id, name], separators=(",", ":")).encode()
    if enabled is None:
        enabled = encryption_enabled(connection, owner, lock=True)
    if not enabled:
        return hashlib.sha256(message).hexdigest()
    return hmac.new(data_key(connection, owner, create=True), message, hashlib.sha256).hexdigest()


def validate_keys(connection) -> None:
    master_key()
    for owner in connection.scalars(select(key_table.c.owner)):
        data_key(connection, owner)
