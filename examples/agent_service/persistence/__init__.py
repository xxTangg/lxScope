# -*- coding: utf-8 -*-
"""Application-owned persistence for lxScope.

This package is intentionally outside ``src/agentscope``.  AgentScope Core
continues to use its configured StorageBase implementation; this package owns
only lxScope business data.
"""

from .database import ApplicationDatabase

__all__ = ["ApplicationDatabase"]
