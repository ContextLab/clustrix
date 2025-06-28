"""Cloud provider integrations for Clustrix."""

from typing import Dict, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from .base import CloudProvider

# Registry of available cloud providers
PROVIDERS: Dict[str, Type["CloudProvider"]] = {}

# Import providers to register them
try:
    from . import aws  # noqa: F401
except ImportError:
    pass

try:
    from . import azure  # noqa: F401
except ImportError:
    pass

try:
    from . import gcp  # noqa: F401
except ImportError:
    pass

try:
    from . import lambda_cloud  # noqa: F401
except ImportError:
    pass

__all__ = ["PROVIDERS"]
