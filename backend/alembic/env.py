"""Alembic environment.

Reads the connection string from app.config rather than alembic.ini, so
migrations can never run against a different database than the app.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.models.activity import Activity  # noqa: F401 - registers the table
from app.models.base import Base
from app.models.otp import EmailOTP  # noqa: F401 - registers the table
from app.models.password_reset import PasswordReset  # noqa: F401 - registers the table
from app.models.user import User  # noqa: F401 - registers the table

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = settings.database_url
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)
config.set_main_option("sqlalchemy.url", url)

# Lets `alembic revision --autogenerate` diff the models against the
# database and write most of a migration for you.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite cannot ALTER most things in place; batch mode rewrites
            # the table instead, so the same migrations run on both engines.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
