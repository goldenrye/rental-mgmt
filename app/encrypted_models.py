"""ORM-facing sensitive properties backed only by an authenticated encrypted payload."""

from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from sqlalchemy import String, Text, event, inspect, select
from sqlalchemy.orm import Mapped, attributes, declared_attr, mapped_column, object_session

from app.database import Base
from app.encryption import EncryptionError, decrypt_record, encrypt_record, encryption_enabled, unit_name_index


class SensitiveField:
    def __init__(self, *, money=False, default=None, required=False):
        self.money = money
        self.default = default
        self.required = required

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        pending = instance.__dict__.get("_pending_secure", {})
        if self.name in pending:
            return pending[self.name]
        if getattr(instance, "encrypted_data", None) is None:
            if inspect(instance).persistent or inspect(instance).detached:
                raise EncryptionError("Encrypted data is missing.")
            return self.default
        values = read_values(instance)
        value = values.get(self.name, self.default)
        return Decimal(value) if self.money and value is not None else value

    def __set__(self, instance, value):
        if inspect(instance).persistent:
            # Load expired columns before recording edits so refresh cannot discard them.
            getattr(instance, "encrypted_data")
        if value is not None and self.money:
            value = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        instance.__dict__.setdefault("_pending_secure", {})[self.name] = value
        if inspect(instance).persistent:
            attributes.flag_dirty(instance)

    def __eq__(self, other):
        raise TypeError(f"{self.name} is encrypted; use an owned-object lookup, not a plaintext SQL predicate.")


class EncryptedRecord:
    encrypted_data: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_owner: Mapped[str] = mapped_column(String(128), nullable=False)

    @declared_attr.directive
    def __mapper_args__(cls):
        return {"version_id_col": cls.encrypted_data, "version_id_generator": False}


def owner_for(connection, table_name, values, existing_owner=None):
    landlord_id = values.get("landlord_id")
    if table_name == "rental_units":
        table = Base.metadata.tables["rental_properties"]
        landlord_id = connection.scalar(select(table.c.landlord_id).where(table.c.id == values["property_id"]))
    elif table_name == "lease_tenants":
        table = Base.metadata.tables["leases"]
        landlord_id = connection.scalar(select(table.c.landlord_id).where(table.c.id == values["lease_id"]))
    if landlord_id is not None:
        return f"landlord:{landlord_id}"
    if table_name == "tenant_profiles":
        # Unassigned self-registered tenants do not yet belong to a landlord.
        return existing_owner or f"unassigned-tenant:{uuid4()}"
    raise EncryptionError("Cannot determine the landlord for encrypted data.")


def instance_owner(connection, instance):
    values = {column.name: getattr(instance, column.name, None) for column in instance.__table__.columns}
    return owner_for(connection, instance.__tablename__, values, instance.encryption_owner)


def read_values(instance, connection=None):
    raw = instance.encrypted_data
    if raw == "pending":
        raise EncryptionError("An incomplete encrypted record was found.")
    signature = (raw, instance.encryption_owner, instance.id)
    cache = instance.__dict__.get("_secure_cache")
    if connection is None:
        session = object_session(instance)
        if session is None:
            if cache and cache[0] == signature:
                return cache[1]
            raise EncryptionError("Encrypted records must be loaded inside a database session.")
        connection = session.connection()
    if instance_owner(connection, instance) != instance.encryption_owner:
        raise EncryptionError("Encrypted record ownership does not match.")
    if cache and cache[0] == signature:
        return cache[1]
    values = decrypt_record(connection, instance.encryption_owner, instance.__tablename__, instance.id, raw)
    instance.__dict__["_secure_cache"] = (signature, values)
    return values


def collect_values(instance, connection, *, new=False):
    values = {} if new else dict(read_values(instance, connection))
    values.update(instance.__dict__.get("_pending_secure", {}))
    for name, field in instance.__sensitive_fields__.items():
        value = values.get(name, field.default)
        if field.required and value is None:
            raise ValueError(f"{name} is required")
        values[name] = str(value) if field.money and value is not None else value
    return values


def before_insert(mapper, connection, instance):
    instance.encryption_owner = instance_owner(connection, instance)
    collect_values(instance, connection, new=True)
    instance.encrypted_data = "pending"
    if instance.__tablename__ == "rental_units":
        instance.name_index = unit_name_index(connection, instance.encryption_owner, instance.property_id, instance.name)


def after_insert(mapper, connection, instance):
    values = collect_values(instance, connection, new=True)
    payload = encrypt_record(connection, instance.encryption_owner, instance.__tablename__, instance.id, values)
    connection.execute(instance.__table__.update().where(instance.__table__.c.id == instance.id)
                       .values(encrypted_data=payload))
    attributes.set_committed_value(instance, "encrypted_data", payload)
    instance.__dict__.pop("_pending_secure", None)
    instance.__dict__.pop("_secure_cache", None)


def before_update(mapper, connection, instance):
    encryption_enabled(connection, instance.encryption_owner, lock=True)
    # Refuse silent ownership changes; transferring encrypted records needs an explicit re-key workflow.
    if instance_owner(connection, instance) != instance.encryption_owner:
        raise EncryptionError("Ownership transfers of encrypted records are not supported.")
    if instance.__dict__.get("_pending_secure"):
        values = collect_values(instance, connection)
        instance.encrypted_data = encrypt_record(connection, instance.encryption_owner, instance.__tablename__, instance.id, values)
        if instance.__tablename__ == "rental_units":
            instance.name_index = unit_name_index(connection, instance.encryption_owner, instance.property_id, values["name"])
        instance.__dict__.pop("_pending_secure", None)
        instance.__dict__.pop("_secure_cache", None)


def clear_cache(instance, *args):
    if instance is not None:
        instance.__dict__.pop("_pending_secure", None)
        instance.__dict__.pop("_secure_cache", None)


def install_encryption(model):
    model.__sensitive_fields__ = {
        name: value for name, value in vars(model).items() if isinstance(value, SensitiveField)
    }
    event.listen(model, "before_insert", before_insert)
    event.listen(model, "after_insert", after_insert)
    event.listen(model, "before_update", before_update)
    event.listen(model, "expire", clear_cache)
    event.listen(model, "refresh", clear_cache)
