"""Add daily reminder chat subscriptions."""

import sqlalchemy as sa
from alembic import op

revision = "0002_reminder_chats"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminder_chats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
    op.create_index(op.f("ix_reminder_chats_chat_id"), "reminder_chats", ["chat_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_reminder_chats_chat_id"), table_name="reminder_chats")
    op.drop_table("reminder_chats")
