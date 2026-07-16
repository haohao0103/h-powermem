"""
HugeGraph storage configuration for PowerMem.

Registers HugeGraph as a graph store provider in the PowerMem storage factory.
Configure via environment variables:
  GRAPH_STORE_HOST=127.0.0.1
  GRAPH_STORE_PORT=8080
  GRAPH_STORE_USER=admin
  GRAPH_STORE_PASSWORD=admin
  GRAPH_STORE_DB_NAME=hugegraph
  GRAPH_STORE_MAX_HOPS=3
"""

from typing import ClassVar, Optional

from pydantic import AliasChoices, Field

from powermem.settings import settings_config
from powermem.storage.config.base import BaseGraphStoreConfig


class HugeGraphConfig(BaseGraphStoreConfig):
    """Configuration for HugeGraph graph store.

    Maps to HugeGraph 1.7.0 REST API connection parameters.
    The HugeGraph server must be running and accessible at the configured host:port.
    """

    _provider_name = "hugegraph"
    _class_path = "powermem.storage.hugegraph.hugegraph_graph.HugeGraphMemoryGraph"

    model_config = settings_config("GRAPH_STORE_", extra="allow", env_file=None)

    # Override defaults for HugeGraph
    port: str = Field(
        default="8080",
        validation_alias=AliasChoices(
            "port",
            "GRAPH_STORE_PORT",
            "HUGEGRAPH_PORT",
        ),
        description="HugeGraph server REST API port (default: 8080)"
    )

    user: str = Field(
        default="admin",
        validation_alias=AliasChoices(
            "user",
            "GRAPH_STORE_USER",
            "HUGEGRAPH_USER",
        ),
        description="HugeGraph admin username"
    )

    password: str = Field(
        default="admin",
        validation_alias=AliasChoices(
            "password",
            "GRAPH_STORE_PASSWORD",
            "HUGEGRAPH_PASSWORD",
            "HUGEGRAPH_PWD",
        ),
        description="HugeGraph admin password"
    )

    db_name: str = Field(
        default="hugegraph",
        validation_alias=AliasChoices(
            "db_name",
            "GRAPH_STORE_DB_NAME",
            "HUGEGRAPH_GRAPH",
            "HUGEGRAPH_NAME",
        ),
        description="HugeGraph graph name (default: hugegraph)"
    )

    # HugeGraph-specific: full URL takes priority
    hugegraph_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "hugegraph_url",
            "HUGEGRAPH_URL",
            "GRAPH_STORE_URL",
        ),
        description="Full HugeGraph REST URL (overrides host+port)"
    )
