"""Local-only recruiting intelligence factory."""

from .models import NormalizedJob
from .paths import DEFAULT_LOCAL_ROOT, local_data_root

__all__ = ["DEFAULT_LOCAL_ROOT", "NormalizedJob", "local_data_root"]
