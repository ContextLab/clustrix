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

try:
    from . import huggingface_spaces  # noqa: F401
except ImportError:
    pass

# Import CloudProviderManager from the old cloud_providers module
try:
    from ..cloud_providers import CloudProviderManager  # noqa: F401
except ImportError:
    pass

__all__ = ["PROVIDERS", "CloudProviderManager"]
