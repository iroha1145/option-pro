"""Expose the backend package when commands start at the repository root.

The deployed image starts inside ``backend`` and imports that package
directly.  Local operator commands commonly start one directory higher.  A
small package path bridge keeps both entry points on the same implementation
without mutating ``sys.path`` or depending on ``PYTHONPATH``.
"""

from __future__ import annotations

from pathlib import Path


_BACKEND_APP = Path(__file__).resolve().parent.parent / "backend" / "app"
if not (_BACKEND_APP / "__init__.py").is_file():
    raise ImportError("Option Pro backend package is unavailable")

__path__ = [str(_BACKEND_APP)]
