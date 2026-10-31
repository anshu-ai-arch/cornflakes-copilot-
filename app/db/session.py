"""
Asynchronous Database Session and Engine setup.
Supports PostgreSQL (default) and SQLite (for unit tests / local runs).
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.core.config import settings

Base = declarative_base()

# Configure engine arguments based on driver
engine_kwargs = {"echo": False, "future": True}

# Create async engine
engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

# Create sessionmaker
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


async def get_db_session() -> AsyncSession:
    """Dependency for obtaining an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db(custom_engine=None):
    """Initializes tables in database."""
    target_engine = custom_engine or engine
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
