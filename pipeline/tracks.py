"""
Track name reconciliation.

There are three different names for the same track in this system:

  1. DevPost / submission form  "Machine Learning & Bio-AI"
  2. Firestore + the judging app  "AI/ML"            <- AUTHORITATIVE
  3. rubrics.json                "Machine Learning / AI"

Firestore naming wins. This module maps (1) -> (2) on import. The (2) -> (3)
hop already exists in src/config/trackRubrics.js as TRACK_ALIASES; keep the two
in sync if a track is ever renamed.

Why this matters: in 2026 a judge opened "Tidal Wave", whose tracks came from
DevPost as "Data Analytics" / "Cloud Development". Neither matched the app's
track list, so the rubric lookup failed, the app silently fell back to a generic
rubric, and the evaluation was written with track=null -- which the leaderboard
then dropped entirely. Four evaluations were lost that way.
"""

from __future__ import annotations

# The authoritative track names, as stored in Firestore and rendered by the app.
CANONICAL: tuple[str, ...] = (
    "AI/ML",
    "Analytics",
    "Cloud",
    "Entrepreneurship & Product",
    "UI/UX & Web Dev",
    "Hardware & IoT",
    "Mechanical Design & Biotechnology",
    "Economics",
)

# DevPost submission-form wording -> canonical.
DEVPOST_TO_CANONICAL: dict[str, str] = {
    "machine learning & bio-ai": "AI/ML",
    "data analytics": "Analytics",
    "cloud development": "Cloud",
    "entrepreneurship & product management": "Entrepreneurship & Product",
    "ui/ux design & web development": "UI/UX & Web Dev",
    "hardware & iot": "Hardware & IoT",
    "mechanical design & biotechnology": "Mechanical Design & Biotechnology",
    "economics": "Economics",
}


class UnknownTrackError(ValueError):
    """Raised when a track name has no canonical mapping."""


def _key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def normalize_track(value: str | None, *, strict: bool = True) -> str | None:
    """Map a single track name to its canonical form.

    Already-canonical names pass through unchanged, so this is safe to run over
    data that has been normalized once already.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None

    for canon in CANONICAL:
        if _key(raw) == _key(canon):
            return canon

    mapped = DEVPOST_TO_CANONICAL.get(_key(raw))
    if mapped:
        return mapped

    if strict:
        raise UnknownTrackError(
            f"No canonical track for {value!r}. Add it to DEVPOST_TO_CANONICAL "
            f"in pipeline/tracks.py -- do not let it through as None, or the "
            f"judge gets the wrong rubric."
        )
    return None


def split_concatenated(value: str) -> list[str]:
    """Recover track names from a cell where two ran together.

    Row 21 of Final_project_info ("AI Plant Companion") lost its surrounding
    quotes, so the CSV comma split the tracks column and the two names ended up
    concatenated as "Entrepreneurship & Product Management Hardware & IoT".
    Longest-first matching pulls them back apart.
    """
    remaining = value.strip()
    found: list[str] = []
    candidates = sorted(DEVPOST_TO_CANONICAL, key=len, reverse=True)

    while remaining:
        for cand in candidates:
            if _key(remaining).startswith(cand):
                found.append(DEVPOST_TO_CANONICAL[cand])
                # Advance past the matched span in the original string.
                consumed = len(remaining) - len(remaining[len(cand):])
                remaining = remaining[consumed:].strip()
                break
        else:
            break

    return found


def normalize_tracks(*values: str | None, strict: bool = True) -> list[str]:
    """Normalize several track cells into a deduplicated canonical list.

    Handles the concatenated-cell case automatically, so callers can feed raw
    CSV columns straight in.
    """
    out: list[str] = []
    for value in values:
        if not value or not value.strip():
            continue
        try:
            canon = normalize_track(value, strict=strict)
            if canon:
                out.append(canon)
        except UnknownTrackError:
            recovered = split_concatenated(value)
            if not recovered:
                if strict:
                    raise
                continue
            out.extend(recovered)

    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]
