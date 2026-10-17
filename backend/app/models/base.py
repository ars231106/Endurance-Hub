from sqlalchemy.orm import declarative_base

# Every model inherits from this. Subclassing it is what registers a table
# with SQLAlchemy's metadata, which is how create_all and Alembic's
# autogenerate know the table exists.
Base = declarative_base()
