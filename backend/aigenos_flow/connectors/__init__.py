"""Managed, provider-backed tools for deterministic aigenos flows.

The connector package deliberately keeps database drivers optional. Importing
``aigenos_flow`` remains lightweight; a provider raises a useful error only
when it is configured or invoked without the ``connectors`` extra installed.
"""

from .manager import ConnectorManager
from .models import (
    ConnectorAdapter,
    ConnectorResult,
    InvocationContext,
    ManagedOperation,
    ParameterSpec,
    ToolDescriptor,
)
from .secrets import SecretResolver
from .store import ConnectorStore

__all__ = [
    "ConnectorAdapter",
    "ConnectorManager",
    "ConnectorResult",
    "ConnectorStore",
    "InvocationContext",
    "ManagedOperation",
    "ParameterSpec",
    "SecretResolver",
    "ToolDescriptor",
]
