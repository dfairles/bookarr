from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_migrations()
    _seed_admin()


def _run_migrations() -> None:
    # Lightweight hand-rolled migrations via PRAGMA instead of Alembic — keeps the
    # deployment dependency-free. Add new columns here as the schema evolves.
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(audiobook_requests)"))
        cols = {row[1] for row in result}
        if "denied_reason" not in cols:
            conn.execute(text("ALTER TABLE audiobook_requests ADD COLUMN denied_reason TEXT NOT NULL DEFAULT ''"))
            conn.commit()


def _seed_admin() -> None:
    """Create an initial admin user if local auth is enabled and the user table is empty.

    Idempotent: does nothing if any user already exists, so it's safe to run on every
    startup without risk of overwriting a changed password.
    """
    if settings.auth_mode.lower() != "local" or not settings.admin_seed_password:
        return
    from sqlalchemy import select
    from app.auth import hash_password
    from app.models import Role, User

    db = SessionLocal()
    try:
        if db.scalar(select(User)) is None:
            db.add(User(
                username="admin",
                hashed_password=hash_password(settings.admin_seed_password),
                role=Role.admin,
            ))
            db.commit()
    finally:
        db.close()


def db_session():
    """FastAPI dependency that provides a per-request SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
