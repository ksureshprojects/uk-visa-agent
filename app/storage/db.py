from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import DB_PATH

from app.storage.models import Base

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
