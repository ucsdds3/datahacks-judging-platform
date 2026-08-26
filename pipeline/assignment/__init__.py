"""Judge -> project assignment for the DataHacks judging platform.

    from pipeline.assignment import solve, SolverConfig, validate

See ``solver.py`` for the algorithm and ``validate.py`` for the standalone
preflight checks.
"""

from .solver import (  # noqa: F401
    GROUP,
    INDIVIDUAL,
    Assignment,
    DisconnectedAssignmentError,
    EmptyTrackError,
    Judge,
    Project,
    SolverConfig,
    solve,
)
from . import validate  # noqa: F401

__all__ = [
    "solve",
    "SolverConfig",
    "Assignment",
    "Judge",
    "Project",
    "INDIVIDUAL",
    "GROUP",
    "DisconnectedAssignmentError",
    "EmptyTrackError",
    "validate",
]
