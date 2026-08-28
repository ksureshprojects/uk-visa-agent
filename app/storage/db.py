from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import DB_PATH

from app.storage.models import Base

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()


_ADDITIVE_COLUMNS = {
    "user_sessions": [("pending_case_ids", "TEXT")],
    "cases": [("conversation_id", "TEXT")],
}


def _add_missing_columns() -> None:
    """create_all() only creates tables that don't exist yet — it won't add
    new columns to a table that's already there. There's no migration
    framework in this project, so handle additive column changes here."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, columns in _ADDITIVE_COLUMNS.items():
        if table not in existing_tables:
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table)}
        for name, ddl_type in columns:
            if name not in existing_columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


def get_session() -> Session:
    return SessionLocal()
