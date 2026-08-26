"""Judge score normalization for the DataHacks judging platform.

    from pipeline.normalization import fit_normalization, load_evaluations

The three runnable scripts are NOT imported here (importing a module that is
also run via ``-m`` makes Python emit a RuntimeWarning). Import them by path:

    from pipeline.normalization.identifiability import diagnose
    from pipeline.normalization.simulate import sweep_anchors

Run them from the repo root:

    python -m pipeline.normalization.identifiability   # can we normalize at all?
    python -m pipeline.normalization.report_2026       # the full 2026 write-up
    python -m pipeline.normalization.simulate          # how many anchors next year?
    pytest pipeline/normalization/test_model.py

ALWAYS run the identifiability check before trusting the model output. The model
returns numbers even when the judge assignment cannot support them;
``identifiability.py`` is what tells you whether those numbers mean anything.
See ``model.py`` for the mixed-effects fit.
"""

from .data import clean, load_evaluations  # noqa: F401
from .model import NormalizationResult, fit_normalization, fit_slice  # noqa: F401

__all__ = [
    "load_evaluations",
    "clean",
    "fit_normalization",
    "fit_slice",
    "NormalizationResult",
]
