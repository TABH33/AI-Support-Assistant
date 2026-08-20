"""encrypt customer pii and add audit_logs table

Revision ID: 6985ed4f7a05
Revises: ca294b499e12
Create Date: 2026-08-20 00:00:00.000000

Task 23 (security & compliance hardening):

  1. Encrypts `customers.full_name`/`email`/`phone_number` at rest --
     changes their column type from VARCHAR to bytea (BYTEA on Postgres,
     BLOB on the SQLite dev/test fallback -- see Task 4's report on why
     SQLite is used when no live Postgres instance is available), and
     re-encrypts every existing row's plaintext value into that new column
     using the exact same helpers the ORM's `TypeDecorator`s use at
     runtime (`app.security.crypto`), so already-inserted data (e.g. a
     live deployment upgraded in place) round-trips correctly through the
     app afterward rather than being silently orphaned or left in
     plaintext. `email` uses `encrypt_deterministic` (matches
     `DeterministicEncryptedString`, required so `Customer.email ==` login
     lookups keep working); `full_name`/`phone_number` use
     `encrypt_randomized` (matches `EncryptedString`). See
     `app/security/crypto.py`'s module docstring for the full scheme/
     tradeoff writeup, and `app/models/customer.py` for where these types
     are wired onto the ORM columns.

     The existing `UniqueConstraint('email')` (from 482fedba36a5's initial
     schema) is left untouched by this migration -- an in-place
     `ALTER COLUMN ... TYPE` (via `USING <col>::bytea` on Postgres) changes
     the column's type without dropping/recreating table-level constraints,
     so there is no need to drop and recreate the unique constraint, and no
     fragile dependency on its (unspecified/DB-assigned) constraint name.
     Deterministic encryption also means the constraint still means exactly
     what it always meant -- unique ciphertext <=> unique plaintext email.

     A fresh `alembic upgrade head` against an empty database (the common
     case for this POC -- see `app/seed/seed.py`, which is always run after
     migrations) has zero existing rows, so the data-rewrite loop below is
     a no-op in that case; it only matters for a database that already has
     plaintext rows in it.

  2. Creates the new `audit_logs` table (Task 23's `AuditLog` model,
     `app/models/audit.py`), which `app/security/audit.py` writes to.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6985ed4f7a05'
down_revision: Union[str, Sequence[str], None] = 'ca294b499e12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Imported inside the function (not at module scope) so a plain
    # `alembic history`/import of this versions module never requires
    # `ENCRYPTION_KEY` to be set unless `upgrade()`/`downgrade()` actually
    # runs -- mirrors this file's own lazy-import-of-app-code need (Alembic
    # versions modules are otherwise dependency-free).
    from app.security.crypto import encrypt_deterministic, encrypt_randomized

    bind = op.get_bind()

    # Capture existing plaintext BEFORE changing the column types below --
    # once the columns are bytea, re-reading them back out as Python `str`
    # would require dialect-specific decoding, so it's simpler to grab the
    # plaintext while the columns are still VARCHAR.
    customers_text = sa.table(
        "customers",
        sa.column("customer_id", sa.Integer()),
        sa.column("full_name", sa.String()),
        sa.column("email", sa.String()),
        sa.column("phone_number", sa.String()),
    )
    existing_rows = bind.execute(
        sa.select(
            customers_text.c.customer_id,
            customers_text.c.full_name,
            customers_text.c.email,
            customers_text.c.phone_number,
        )
    ).fetchall()

    with op.batch_alter_table("customers") as batch_op:
        # `postgresql_using` is only applied on the postgresql dialect
        # (ignored on SQLite's batch-recreate path) -- Postgres has no
        # implicit/assignment cast from character varying to bytea, so an
        # explicit `USING col::bytea` is required (this is the standard,
        # well-defined Postgres idiom for "reinterpret this text's own
        # encoded bytes as a bytea value").
        batch_op.alter_column(
            "full_name",
            existing_type=sa.String(length=255),
            type_=sa.LargeBinary(),
            existing_nullable=False,
            postgresql_using="full_name::bytea",
        )
        batch_op.alter_column(
            "email",
            existing_type=sa.String(length=255),
            type_=sa.LargeBinary(),
            existing_nullable=False,
            postgresql_using="email::bytea",
        )
        batch_op.alter_column(
            "phone_number",
            existing_type=sa.String(length=32),
            type_=sa.LargeBinary(),
            existing_nullable=False,
            postgresql_using="phone_number::bytea",
        )

    customers_bin = sa.table(
        "customers",
        sa.column("customer_id", sa.Integer()),
        sa.column("full_name", sa.LargeBinary()),
        sa.column("email", sa.LargeBinary()),
        sa.column("phone_number", sa.LargeBinary()),
    )
    for row in existing_rows:
        bind.execute(
            customers_bin.update()
            .where(customers_bin.c.customer_id == row.customer_id)
            .values(
                full_name=encrypt_randomized(row.full_name),
                email=encrypt_deterministic(row.email),
                phone_number=encrypt_randomized(row.phone_number),
            )
        )

    op.create_table(
        "audit_logs",
        sa.Column("audit_log_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("audit_log_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    from app.security.crypto import decrypt_deterministic, decrypt_randomized

    op.drop_table("audit_logs")

    bind = op.get_bind()

    customers_bin = sa.table(
        "customers",
        sa.column("customer_id", sa.Integer()),
        sa.column("full_name", sa.LargeBinary()),
        sa.column("email", sa.LargeBinary()),
        sa.column("phone_number", sa.LargeBinary()),
    )
    existing_rows = bind.execute(
        sa.select(
            customers_bin.c.customer_id,
            customers_bin.c.full_name,
            customers_bin.c.email,
            customers_bin.c.phone_number,
        )
    ).fetchall()
    decrypted = [
        (
            row.customer_id,
            decrypt_randomized(row.full_name),
            decrypt_deterministic(row.email),
            decrypt_randomized(row.phone_number),
        )
        for row in existing_rows
    ]

    with op.batch_alter_table("customers") as batch_op:
        batch_op.alter_column(
            "full_name",
            existing_type=sa.LargeBinary(),
            type_=sa.String(length=255),
            existing_nullable=False,
            postgresql_using="convert_from(full_name, 'UTF8')",
        )
        batch_op.alter_column(
            "email",
            existing_type=sa.LargeBinary(),
            type_=sa.String(length=255),
            existing_nullable=False,
            postgresql_using="convert_from(email, 'UTF8')",
        )
        batch_op.alter_column(
            "phone_number",
            existing_type=sa.LargeBinary(),
            type_=sa.String(length=32),
            existing_nullable=False,
            postgresql_using="convert_from(phone_number, 'UTF8')",
        )

    customers_text = sa.table(
        "customers",
        sa.column("customer_id", sa.Integer()),
        sa.column("full_name", sa.String()),
        sa.column("email", sa.String()),
        sa.column("phone_number", sa.String()),
    )
    for customer_id, full_name, email, phone_number in decrypted:
        bind.execute(
            customers_text.update()
            .where(customers_text.c.customer_id == customer_id)
            .values(full_name=full_name, email=email, phone_number=phone_number)
        )
