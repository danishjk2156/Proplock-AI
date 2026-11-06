"""listing photos belong to listings

Revision ID: b95ffddc5bec
Revises: ff58467df755
Create Date: 2026-11-04 16:22:07.395633

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.db.base


# revision identifiers, used by Alembic.
revision: str = 'b95ffddc5bec'
down_revision: Union[str, Sequence[str], None] = 'ff58467df755'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # listing_photos is always empty before this revision (no code wrote to it), so the
    # new NOT NULL columns need no default for existing rows.
    with op.batch_alter_table('listing_photos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('listing_id', sa.String(length=36), nullable=False))
        batch_op.add_column(sa.Column('gps_source', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('removed_at', app.db.base.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('created_at', app.db.base.UTCDateTime(), nullable=False))
        batch_op.alter_column('version_id',
               existing_type=sa.VARCHAR(length=36),
               nullable=True)
        batch_op.create_index(batch_op.f('ix_listing_photos_listing_id'), ['listing_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_listing_photos_listing_id_listings', 'listings', ['listing_id'], ['id'], ondelete='CASCADE'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('listing_photos', schema=None) as batch_op:
        batch_op.drop_constraint('fk_listing_photos_listing_id_listings', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_listing_photos_listing_id'))
        batch_op.alter_column('version_id',
               existing_type=sa.VARCHAR(length=36),
               nullable=False)
        batch_op.drop_column('created_at')
        batch_op.drop_column('removed_at')
        batch_op.drop_column('gps_source')
        batch_op.drop_column('listing_id')
