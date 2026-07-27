"""add token revocation (revoked_tokens table + users.token_version)

OQ-6：引入 Token 吊销能力。两张机制互补：
- revoked_tokens：单会话登出黑名单（按 jti 主键点查）。
- users.token_version：全量吊销计数器，bump 后旧版本 token 立即失效。

Revision ID: a1f2c3d4e5b6
Revises: df3cf6adbab3
Create Date: 2026-07-27 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f2c3d4e5b6'
down_revision: Union[str, None] = 'df3cf6adbab3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 吊销黑名单表：jti 为主键，按 jti 点查
    op.create_table(
        'revoked_tokens',
        sa.Column('jti', sa.String(32), primary_key=True),
        sa.Column('exp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.String(32), nullable=True),
        sa.Column(
            'revoked_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # users 增加 token_version 列（全量吊销计数器）
    # SQLite 不支持直接 ADD COLUMN ... NOT NULL 无默认，故 server_default='0'；
    # PostgreSQL 上 render_as_batch=True 也安全。
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'token_version',
                sa.Integer(),
                nullable=False,
                server_default=sa.text('0'),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('token_version')

    op.drop_table('revoked_tokens')
