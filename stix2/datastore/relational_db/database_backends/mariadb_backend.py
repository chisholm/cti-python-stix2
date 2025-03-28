import os
from typing import Any

from sqlalchemy import VARCHAR, Text
from sqlalchemy.schema import CreateSchema

from stix2.base import (
    _DomainObject, _MetaObject, _Observable, _RelationshipObject,
)
from stix2.datastore.relational_db.utils import schema_for

from .database_backend_base import DatabaseBackend


class MariaDBBackend(DatabaseBackend):
    default_database_connection_url = \
        f"mariadbsql://{os.getenv('MARIADB_USER')}:" + \
        f"{os.getenv('MARIADB_PASSWORD')}@" + \
        f"{os.getenv('MARIADB_IP_ADDRESS')}:" + \
        f"{os.getenv('MARIADB_PORT', '3306')}/rdb"

    def __init__(self, database_connection_url=default_database_connection_url, force_recreate=False, **kwargs: Any):
        super().__init__(database_connection_url, force_recreate=force_recreate, **kwargs)

    # =========================================================================
    # sql type methods (overrides)

    @staticmethod
    def determine_sql_type_for_key_as_id():  # noqa: F811
        return VARCHAR(255)

    @staticmethod
    def determine_sql_type_for_binary_property():  # noqa: F811
        return MariaDBBackend.determine_sql_type_for_string_property()

    @staticmethod
    def determine_sql_type_for_hex_property():  # noqa: F811
        # return LargeBinary
        return MariaDBBackend.determine_sql_type_for_string_property()

    @staticmethod
    def determine_sql_type_for_timestamp_property():  # noqa: F811
        return Text

    # =========================================================================
    # Other methods

    @staticmethod
    def array_allowed():
        return False

    def create_regex_constraint_clause(self, column_name, pattern):
        return f"{column_name} REGEXP {pattern}"
