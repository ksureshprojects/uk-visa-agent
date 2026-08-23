import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.storage.models import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
