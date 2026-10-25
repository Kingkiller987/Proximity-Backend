from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Set sqlalchemy.url from our settings if not set via CLI
settings = get_settings()
# Allow override via environment variable or CLI -x db=test
x_args = context.get_x_argument(as_dictionary=True)
is_test = x_args.get("db") == "test"
db_url = settings.get_database_url(is_test=is_test)
config.set_main_option("sqlalchemy.url", db_url)

POSTGIS_TABLES = {
    "spatial_ref_sys", "topology", "layer", "featnames",
    "geocode_settings", "geocode_settings_default", "direction_lookup",
    "secondary_unit_lookup", "state_lookup", "street_type_lookup",
    "place_lookup", "county_lookup", "countysub_lookup", "zip_lookup_all",
    "zip_lookup_base", "zip_lookup", "county", "state", "place", "zip_state",
    "zip_state_loc", "cousub", "edges", "addrfeat", "addr", "zcta5",
    "tabblock20", "faces", "loader_platform", "loader_variables",
    "loader_lookuptables", "tract", "tabblock", "bg", "pagc_gaz",
    "pagc_lex", "pagc_rules",
}


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name in POSTGIS_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
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
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
