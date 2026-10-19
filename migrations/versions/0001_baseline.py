"""Baseline: pgvector extension and the shared tables eval_run, eval_result, llm_call.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-19

Written by the agent, applied by the human (BACKEND_SCHEMA §S6). Schema per
BACKEND_SCHEMA §S1-§S2; tests/integration/test_migrations.py checks it matches the models.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column[object]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    # gen_random_uuid() is built into PostgreSQL 13+; `vector` ships with pgvector/pgvector:pg16.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "eval_run",
        _id(),
        _created_at(),
        sa.Column("run_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dataset_split", sa.Text(), nullable=False),
        sa.Column("dataset_hash", sa.Text(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latency_p50_ms", sa.Integer(), nullable=True),
        sa.Column("latency_p95_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.BigInteger(), nullable=True),
        sa.Column("tokens_out", sa.BigInteger(), nullable=True),
        sa.Column("list_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('completed', 'partial', 'failed')", name=op.f("ck_eval_run_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_run")),
        sa.UniqueConstraint("run_key", name=op.f("uq_eval_run_run_key")),
    )

    op.create_table(
        "eval_result",
        _id(),
        _created_at(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("score", sa.Double(), nullable=True),
        sa.Column("prediction", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gold", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("failure_label", sa.Text(), nullable=True),
        sa.Column("failure_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pass', 'fail', 'error')", name=op.f("ck_eval_result_status")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["eval_run.id"],
            name=op.f("fk_eval_result_run_id_eval_run"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_result")),
        sa.UniqueConstraint("run_id", "case_id", name=op.f("uq_eval_result_run_id_case_id")),
    )
    op.create_index("ix_eval_result_run_id_status", "eval_result", ["run_id", "status"])

    op.create_table(
        "llm_call",
        _id(),
        _created_at(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("case_id", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("retries", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("schema_valid", sa.Boolean(), nullable=True),
        sa.Column("list_cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["eval_run.id"], name=op.f("fk_llm_call_run_id_eval_run")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_call")),
    )
    op.create_index("ix_llm_call_run_id", "llm_call", ["run_id"])
    op.create_index("ix_llm_call_created_at", "llm_call", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_call_created_at", table_name="llm_call")
    op.drop_index("ix_llm_call_run_id", table_name="llm_call")
    op.drop_table("llm_call")
    op.drop_index("ix_eval_result_run_id_status", table_name="eval_result")
    op.drop_table("eval_result")
    op.drop_table("eval_run")
    op.execute("DROP EXTENSION IF EXISTS vector")
