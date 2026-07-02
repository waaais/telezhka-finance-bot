"""Initial finance accounting schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("salary_amount", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_employees_name"), "employees", ["name"], unique=True)

    op.create_table(
        "finance_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("employee_name", sa.String(length=80), nullable=False),
        sa.Column("cash", sa.Integer(), nullable=False),
        sa.Column("cashless", sa.Integer(), nullable=False),
        sa.Column("revenue", sa.Integer(), nullable=False),
        sa.Column("salary", sa.Integer(), nullable=False),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_chat_id", "source_message_id", name="uq_entry_source_message"),
    )
    op.create_index(
        op.f("ix_finance_entries_employee_id"),
        "finance_entries",
        ["employee_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_finance_entries_employee_name"),
        "finance_entries",
        ["employee_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_finance_entries_entry_date"),
        "finance_entries",
        ["entry_date"],
        unique=False,
    )

    op.create_table(
        "processed_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("entry_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entry_id"], ["finance_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_processed_message_source"),
    )


def downgrade() -> None:
    op.drop_table("processed_messages")
    op.drop_index(op.f("ix_finance_entries_entry_date"), table_name="finance_entries")
    op.drop_index(op.f("ix_finance_entries_employee_name"), table_name="finance_entries")
    op.drop_index(op.f("ix_finance_entries_employee_id"), table_name="finance_entries")
    op.drop_table("finance_entries")
    op.drop_index(op.f("ix_employees_name"), table_name="employees")
    op.drop_table("employees")
