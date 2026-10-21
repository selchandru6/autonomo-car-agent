"""AI car shopping: auditable monitor for new 2026 BMW X5 xDrive40i listings in Texas."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("carshop")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"
