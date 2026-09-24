import os

from alembic import context
from dotenv import load_dotenv

from app.database import Base, make_engine
from app import models

load_dotenv()
url = os.getenv("DATABASE_URL", "sqlite:///./food-diary.db")
if context.is_offline_mode():
    if url.startswith(("postgres://", "postgresql://")):
        url = "postgresql+psycopg://" + url.split("://", 1)[1]
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

