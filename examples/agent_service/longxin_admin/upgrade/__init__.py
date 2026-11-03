"""Application and AgentScope core upgrade support for the Longxin layer."""

from .router import upgrade_router
from .service import UpgradeService

__all__ = ["UpgradeService", "upgrade_router"]
