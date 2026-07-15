"""
Job Search AI Agent System

A stateful, SQLite-backed job search assistant built with Claude.
"""

__version__ = "1.0.0"

from src.tools import search_jobs

from src.config import (
    DEFAULT_JOB_ROLE,
    DEFAULT_LOCATION,
    DEFAULT_NUM_RESULTS,
    validate_config,
)

__all__ = [
    # Tools
    "search_jobs",
    # Config
    "DEFAULT_JOB_ROLE",
    "DEFAULT_LOCATION",
    "DEFAULT_NUM_RESULTS",
    "validate_config",
]
