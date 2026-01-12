import logging

from app.db.base import Base
from app.db.session import engine


LOG = logging.getLogger(__name__)


async def init_db() -> None:
    """Create database tables if they do not exist.

    This acts as a simple bootstrap in environments without Alembic migrations.
    Safe to run multiple times.
    """

    try:
        async with engine.begin() as conn:
            # Use checkfirst=True to prevent errors if tables already exist
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
        LOG.info("database initialized: ensured tables exist")
    except Exception as exc:
        # Check if it's just a "already exists" error
        if "already exists" in str(exc):
            LOG.info("database initialization: tables already exist")
        else:
            LOG.info("database initialization failed err=%s", exc)
            # Don't raise - allow the application to continue
            pass


