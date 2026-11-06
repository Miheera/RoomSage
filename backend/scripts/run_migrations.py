"""Apply ordered raw SQL migrations to the configured PostgreSQL database."""

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from config import settings


def main() -> None:
    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    # Fail quickly with an actionable error if Docker/PostgreSQL is unavailable.
    migration_engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    print("Connecting to PostgreSQL and checking migrations...", flush=True)
    try:
        with migration_engine.begin() as connection:
            connection.exec_driver_sql("SET statement_timeout = '60s'")
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(255) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            ))
            applied = set(connection.execute(text("SELECT version FROM schema_migrations")).scalars())
            for path in sorted(migration_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")):
                if path.name in applied:
                    print(f"Already applied: {path.name}", flush=True)
                    continue
                print(f"Applying {path.name}...", flush=True)
                sql = path.read_text(encoding="utf-8")
                for statement in (part.strip() for part in sql.split(";")):
                    if statement:
                        connection.exec_driver_sql(statement)
                connection.execute(
                    text("INSERT INTO schema_migrations(version) VALUES (:version)"),
                    {"version": path.name},
                )
                print(f"Applied {path.name}", flush=True)
    except OperationalError as exc:
        raise RuntimeError(
            "Could not connect to PostgreSQL. Start Docker Desktop and the backend "
            "postgres service, then retry. The connection attempt timed out after 5 seconds."
        ) from exc
    finally:
        migration_engine.dispose()


if __name__ == "__main__":
    main()
