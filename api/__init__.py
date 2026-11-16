"""Pacote de API REST e interface de linha de comando (CLI)."""

__version__ = "0.1.0"

from api.app import app  # noqa: E402

__all__ = ["__version__", "app"]
