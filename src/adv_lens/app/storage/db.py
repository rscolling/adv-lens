from collections.abc import Iterator

from sqlmodel import Session, create_engine

from adv_lens.app.settings import settings

engine = create_engine(settings.postgres_dsn, echo=False, pool_pre_ping=True)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
