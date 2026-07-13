"""Isolated MacroLens catalyst cache and synchronisation subsystem.

Importing this package is deliberately side-effect free.  In particular it
does not open SQLite or create an HTTP client; those operations only happen in
the API request handlers and the dedicated worker process.
"""

from .config import CatalystSettings, get_catalyst_settings

__all__ = ["CatalystSettings", "get_catalyst_settings"]
