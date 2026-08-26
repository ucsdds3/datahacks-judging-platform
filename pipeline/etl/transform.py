"""
Pure transforms: raw ``SourceRow``s in, validated records out.

Nothing in this module opens a file, talks to Firestore, or reads a clock. Every
function here is deterministic -- the same input rows always produce the same
records, in the same order, with the same IDs. That is what makes the pipeline
idempotent, which is the property the four old Node scripts lacked and the
reason production ended up with 210 judge docs in three ID formats and 212
synthetic projects mixed in with the real ones.

Three rules this module follows without exception:

1. **Never silently drop a record.** Anything that cannot be turned into a valid
   document becomes a ``Rejection`` with a reason and a line number, and shows
   up in the validation report. The run then refuses to load unless a human
   explicitly allows it.
2. **Never silently null a field.** An unrecognised track raises. In 2026 a
   ``None`` track made the app fall back to a generic rubric and the leaderboard
   dropped four evaluations.
3. **Never invent an ID.** IDs are a pure function of the source data, and a
   collision raises instead of overwriting.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Set, Tuple

from pipeline.tracks import UnknownTrackError, normalize_track, normalize_tracks

from .extract import (
    DEVPOST_SOURCE,
    PROJECT_FIRST_TRACK_COLUMN,
    PROJECT_SECOND_TRACK_COLUMN,
    PROJECT_TABLE_COLUMN,
    PROJECT_TITLE_COLUMN,
    PROJECT_TRACKS_COLUMN,
    SourceRow,
)

# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class TransformError(RuntimeError):
    """Base class for transform-stage failures."""


class IdCollisionError(TransformError):
    """Two different source records produced the same document ID.

    Always fatal. The old scripts would have written the second record on top of
    the first and reported success.
    """


# --------------------------------------------------------------------------
# sponsor challenges
# --------------------------------------------------------------------------

# Award tracks that have no submissions of their own. Judges assigned to these
# are scored separately and must NOT be fed to the room-assignment solver.
# They are legitimate values in the Tracks column, so they cannot be treated as
# unknown tracks -- but they are not canonical tracks either, so they cannot go
# through pipeline.tracks. They are enumerated here, deliberately: a *new*
# sponsor challenge must be added by a human, not guessed at.
#
# The same list exists in pipeline/assignment/replay_2026.py as CHALLENGE_TRACKS.
# Keep the two in sync if a sponsor is added or renamed.
SPONSOR_CHALLENGES: Tuple[str, ...] = (
    "DataBricks Challenge",
    "ZenPower Challenge",
    "Marimo Challenge",
    "Best Use of Scripps Data",
)

_SPONSOR_LOOKUP = {c.strip().lower(): c for c in SPONSOR_CHALLENGES}


def classify_track(value: str) -> Tuple[str, Optional[str]]:
    """Classify a Tracks cell as a canonical track or a sponsor challenge.

    Returns ``(kind, name)`` where kind is ``"track"``, ``"sponsor"`` or
    ``"empty"``. Raises ``UnknownTrackError`` for anything else -- a value we do
    not recognise is a data problem a human has to look at, never a ``None``.
    """
    text = (value or "").strip()
    if not text:
        return ("empty", None)

    sponsor = _SPONSOR_LOOKUP.get(" ".join(text.lower().split()))
    if sponsor:
        return ("sponsor", sponsor)

    # Raises UnknownTrackError for unrecognised values. That is the point.
    return ("track", normalize_track(text, strict=True))


# --------------------------------------------------------------------------
# ID scheme
# --------------------------------------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_TABLE_NUMBER_MAX = 1000


def slugify(text: str) -> str:
    """Lowercase, collapse every non-``[a-z0-9]`` run to a single ``-``, trim.

    ``"AI Plant Companion"`` -> ``"ai-plant-companion"``.
    """
    return _SLUG_STRIP.sub("-", (text or "").lower()).strip("-")


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def project_id(title: str) -> str:
    """Document ID for a project.

    Scheme
    ------
    ``slugify(title)``. Stable for as long as the title is stable, readable in
    the Firestore console, and identical to the scheme the surviving real
    ``projects`` documents already use -- so re-running this pipeline updates
    those documents instead of creating a second generation beside them.

    Fallback
    --------
    A title made entirely of non-ASCII characters slugs to the empty string. The
    2026 event really did have a project titled ``"ㄖ"``. Rather than emit an
    empty document ID (Firestore rejects it) or a positional one (not stable
    across runs), those fall back to ``"project-" + sha1(title)[:10]``, which is
    still a pure function of the title.

    Raises
    ------
    ValueError if the title is blank -- there is no stable ID for a nameless
    record, and guessing one is how duplicates get created.
    """
    if not (title or "").strip():
        raise ValueError("project_id() needs a non-empty title.")
    slug = slugify(title)
    if not slug:
        return "project-" + _digest(title.strip())
    return slug


def judge_id(username: str, name: str) -> str:
    """Document ID for a judge.

    Scheme
    ------
    The ``Username`` column from the credentials sheet, verbatim
    (``"AarushiBajaj"``). This is what production's ``judges`` collection is
    keyed by, what the login flow issues, and what ``evaluations`` resolve back
    to through Firebase Auth. Changing it would orphan every existing
    evaluation, so it is fixed.

    Fallback
    --------
    A judge with no username (on the roster but never checked in) gets
    ``"judge-" + sha1(casefolded name)[:10]``. The ``judge-`` prefix makes these
    trivially greppable, since they cannot log in until an organiser issues a
    real username.

    Note this is deliberately NOT the ``name-slug`` scheme (``anuj-jain``) used
    by the old ``reset-and-upload.js``, nor the ``email-slug`` scheme
    (``john-doe-gmail-com``) used by ``assign.js``. Those two produced the
    duplicate generations still sitting in production; neither is reachable
    from here.

    Raises
    ------
    ValueError if both username and name are blank.
    """
    username = (username or "").strip()
    if username:
        return username
    name = (name or "").strip()
    if not name:
        raise ValueError("judge_id() needs a username or a name.")
    return "judge-" + _digest(" ".join(name.lower().split()))


class IdRegistry:
    """Assigns IDs and refuses to hand the same one out twice.

    ``claim`` raises on collision rather than returning a suffixed variant: a
    silent ``-2`` suffix would mean two runs of the pipeline could assign the
    same suffix to different records depending on row order, which breaks
    idempotency. A collision is a genuine ambiguity and needs a human.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._owners: Dict[str, str] = {}

    def claim(self, doc_id: str, owner: str) -> str:
        """Record ``doc_id`` as belonging to ``owner`` (a human-readable label)."""
        existing = self._owners.get(doc_id)
        if existing is not None and existing != owner:
            raise IdCollisionError(
                f"{self.kind} ID {doc_id!r} is claimed by two different records:\n"
                f"  1. {existing}\n"
                f"  2. {owner}\n"
                f"Refusing to continue -- writing both would silently overwrite "
                f"one with the other. Rename one of them at the source, then "
                f"re-run."
            )
        self._owners[doc_id] = owner
        return doc_id

    def __contains__(self, doc_id: object) -> bool:
        return doc_id in self._owners

    def __len__(self) -> int:
        return len(self._owners)


# --------------------------------------------------------------------------
# report types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rejection:
    """A record that could not be made valid. Reported, never silently dropped."""

    source: str
    line_number: int
    identity: str
    reason: str
    detail: str = ""

    def render(self) -> str:
        tail = f" -- {self.detail}" if self.detail else ""
        return (
            f"{self.source} line {self.line_number}: {self.identity!r} "
            f"[{self.reason}]{tail}"
        )


@dataclass(frozen=True)
class Note:
    """Something a human should see but that does not block the load."""

    source: str
    line_number: int
    identity: str
    kind: str
    detail: str = ""

    def render(self) -> str:
        tail = f" -- {self.detail}" if self.detail else ""
        return (
            f"{self.source} line {self.line_number}: {self.identity!r} "
            f"[{self.kind}]{tail}"
        )


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectRecord:
    """One ``projects/{id}`` document.

    ``assignedJudges`` is deliberately absent: that field is owned by the
    assignment solver, and the loader merges rather than replaces, so running
    the ETL never wipes an assignment that has already shipped.
    """

    id: str
    name: str
    tracks: Tuple[str, ...]
    table_number: Optional[int]
    source_line: int = 0  # where in the project list it came from; not written
    provenance: str = "project_list"
    needs_review: bool = False
    review_reason: str = ""
    submission_url: str = ""
    description: str = ""
    built_with: str = ""
    video_url: str = ""
    github_url: str = ""
    sponsor_challenges: Tuple[str, ...] = ()

    def to_document(self) -> Dict[str, object]:
        """The Firestore/parquet payload. Key names match what src/ reads."""
        doc: Dict[str, object] = {
            "name": self.name,
            "tracks": list(self.tracks),
            "tableNumber": self.table_number,
            "provenance": self.provenance,
            "needsReview": self.needs_review,
        }
        if self.review_reason:
            doc["reviewReason"] = self.review_reason
        for key, value in (
            ("submissionUrl", self.submission_url),
            ("description", self.description),
            ("builtWith", self.built_with),
            ("videoUrl", self.video_url),
            ("githubUrl", self.github_url),
        ):
            if value:
                doc[key] = value
        if self.sponsor_challenges:
            doc["sponsorChallenges"] = list(self.sponsor_challenges)
        return doc


@dataclass(frozen=True)
class JudgeRecord:
    """One ``judges/{id}`` document.

    ``assignedProjects`` is absent for the same reason ``assignedJudges`` is
    absent from ProjectRecord. The ``Password`` column from the credentials
    sheet is absent on purpose too -- see ``build_judges``.
    """

    id: str
    name: str
    email: str
    track: Optional[str]
    sponsor_challenge: Optional[str]
    username: str
    checked_in: bool
    company: str = ""
    role: str = ""
    format: str = ""
    needs_review: bool = False
    review_reason: str = ""

    def to_document(self) -> Dict[str, object]:
        doc: Dict[str, object] = {
            "name": self.name,
            "email": self.email,
            "track": self.track,
            "username": self.username,
            "checkedIn": self.checked_in,
            "company": self.company,
            "role": self.role,
            "format": self.format,
            "needsReview": self.needs_review,
        }
        if self.sponsor_challenge:
            doc["sponsorChallenge"] = self.sponsor_challenge
        if self.review_reason:
            doc["reviewReason"] = self.review_reason
        return doc


@dataclass
class TransformResult:
    """Records that passed, plus everything a human needs to see."""

    records: List[object] = field(default_factory=list)
    rejections: List[Rejection] = field(default_factory=list)
    notes: List[Note] = field(default_factory=list)
    rows_in: int = 0

    @property
    def rows_out(self) -> int:
        return len(self.records)


# --------------------------------------------------------------------------
# small validators
# --------------------------------------------------------------------------


def parse_table_number(raw: str) -> int:
    """Parse a table number, or raise ``ValueError`` with a usable message."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("table number is blank")
    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"table number {text!r} is not an integer") from None
    if value <= 0:
        raise ValueError(f"table number {value} is not positive")
    if value > _TABLE_NUMBER_MAX:
        raise ValueError(
            f"table number {value} exceeds the sanity limit of {_TABLE_NUMBER_MAX}"
        )
    return value


def looks_like_email(value: str) -> bool:
    """Cheap structural check. Not RFC 5322 -- we only need to catch the case
    where a last name ended up in the Email column, which happened in 2026."""
    text = (value or "").strip()
    if text.count("@") != 1:
        return False
    local, _, domain = text.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".")


def _title_key(title: str) -> str:
    """Comparison key for project titles: case- and whitespace-insensitive."""
    return " ".join((title or "").strip().lower().split())


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------


def build_projects(
    rows: Sequence[SourceRow],
    *,
    registry: Optional[IdRegistry] = None,
) -> TransformResult:
    """Turn project-list rows into ``ProjectRecord``s.

    Rejects (reported, not dropped silently): blank title, unparseable table
    number, duplicate title, duplicate table number, unresolvable track.
    Raises immediately on an ID collision between two distinct titles.
    """
    registry = registry if registry is not None else IdRegistry("project")
    result = TransformResult(rows_in=len(rows))

    seen_titles: Dict[str, SourceRow] = {}
    seen_tables: Dict[int, str] = {}

    # Walk the rows in file order, whatever order the caller handed them over
    # in. When two rows conflict (same title, same table number) the earlier
    # line wins -- so the winner is a property of the spreadsheet, not of how
    # the rows happened to be shuffled on the way here.
    for row in sorted(rows, key=lambda r: (r.source, r.line_number)):
        title = row.text(PROJECT_TITLE_COLUMN)
        identity = title or "<blank title>"

        if not title:
            result.rejections.append(
                Rejection(
                    row.source, row.line_number, identity, "missing_title",
                    "every project needs a title; it is the ID source",
                )
            )
            continue

        key = _title_key(title)
        if key in seen_titles:
            first = seen_titles[key]
            result.rejections.append(
                Rejection(
                    row.source, row.line_number, identity, "duplicate_title",
                    f"already seen at line {first.line_number}",
                )
            )
            continue

        try:
            table_number = parse_table_number(row.text(PROJECT_TABLE_COLUMN))
        except ValueError as exc:
            result.rejections.append(
                Rejection(row.source, row.line_number, identity, "bad_table_number", str(exc))
            )
            continue

        if table_number in seen_tables:
            result.rejections.append(
                Rejection(
                    row.source, row.line_number, identity, "duplicate_table_number",
                    f"table {table_number} already used by {seen_tables[table_number]!r}",
                )
            )
            continue

        # normalize_tracks handles the run-together cells left behind by the
        # unquoted-tracks repair in extract.py.
        try:
            canonical = normalize_tracks(
                row.text(PROJECT_TRACKS_COLUMN),
                row.text(PROJECT_FIRST_TRACK_COLUMN),
                row.text(PROJECT_SECOND_TRACK_COLUMN),
                strict=True,
            )
        except UnknownTrackError as exc:
            result.rejections.append(
                Rejection(row.source, row.line_number, identity, "unknown_track", str(exc))
            )
            continue

        if not canonical:
            result.rejections.append(
                Rejection(
                    row.source, row.line_number, identity, "no_tracks",
                    "a project with no track cannot be assigned or scored",
                )
            )
            continue

        doc_id = registry.claim(project_id(title), f"{row.where()} {title!r}")

        seen_titles[key] = row
        seen_tables[table_number] = title
        if row.repaired:
            for note in row.notes:
                result.notes.append(
                    Note(row.source, row.line_number, identity, "repaired_row", note)
                )

        result.records.append(
            ProjectRecord(
                id=doc_id,
                name=title,
                tracks=tuple(canonical),
                table_number=table_number,
                source_line=row.line_number,
            )
        )

    result.records.sort(key=lambda r: r.id)
    return result


# --------------------------------------------------------------------------
# DevPost enrichment + synthetic-vs-real classification
# --------------------------------------------------------------------------


def _devpost_field(row: SourceRow, *names: str) -> str:
    """First non-empty value among ``names``. Handles the duplicated
    "Video Demo Link" column, where the useful value may be in either copy."""
    for name in names:
        value = row.text(name)
        if value:
            return value
    return ""


def build_devpost_index(rows: Sequence[SourceRow]) -> Dict[str, SourceRow]:
    """Index a DevPost export by title key. Later duplicates are ignored and
    surfaced by ``classify_provenance``."""
    index: Dict[str, SourceRow] = {}
    for row in sorted(rows, key=lambda r: (r.source, r.line_number)):
        key = _title_key(row.text(PROJECT_TITLE_COLUMN))
        if key and key not in index:
            index[key] = row
    return index


def classify_provenance(
    projects: Sequence[ProjectRecord],
    devpost_rows: Optional[Sequence[SourceRow]],
) -> Tuple[List[ProjectRecord], List[Note]]:
    """Flag records that are not backed by a real DevPost submission.

    NOTHING IS DELETED. Records absent from the submission export come back with
    ``provenance="unverified"`` and ``needs_review=True`` so an organiser can
    decide. That is the guard against a repeat of 2026, where 212 synthetic
    projects sat in production indistinguishable from the 160 real ones -- and
    the fix is a human looking at a list, not a script running DELETE.

    Passing ``devpost_rows=None`` (the export has not arrived yet) marks every
    record ``provenance="unverified_no_export"`` rather than pretending they are
    all real.
    """
    notes: List[Note] = []

    if devpost_rows is None:
        out = [
            replace(
                p,
                provenance="unverified_no_export",
                needs_review=True,
                review_reason="no DevPost export supplied; provenance unknown",
            )
            for p in projects
        ]
        notes.append(
            Note(
                DEVPOST_SOURCE, 0, "<all projects>", "no_devpost_export",
                "pass --devpost <path> once the real export exists to separate "
                "real submissions from synthetic leftovers",
            )
        )
        return out, notes

    index = build_devpost_index(devpost_rows)
    matched: Set[str] = set()
    out: List[ProjectRecord] = []

    for project in projects:
        key = _title_key(project.name)
        row = index.get(key)
        if row is None:
            out.append(
                replace(
                    project,
                    provenance="unverified",
                    needs_review=True,
                    review_reason=(
                        "no matching DevPost submission; likely synthetic or a "
                        "title mismatch -- confirm before judging"
                    ),
                )
            )
            notes.append(
                Note(
                    "project_list", project.source_line, project.name,
                    "unverified_project",
                    "not present in the DevPost export",
                )
            )
            continue

        matched.add(key)
        sponsors = tuple(
            s.strip()
            for s in _devpost_field(
                row, "Which Sponsor Challenges Are You Submitting To?"
            ).split(",")
            if s.strip() and s.strip() != "-"
        )
        out.append(
            replace(
                project,
                provenance="devpost",
                needs_review=False,
                review_reason="",
                submission_url=_devpost_field(row, "Submission Url"),
                description=_devpost_field(row, "About The Project"),
                built_with=_devpost_field(row, "Built With"),
                video_url=_devpost_field(
                    row, "Video Demo Link", "Video Demo Link (2)", "Product Demo Link"
                ),
                github_url=_devpost_field(row, "Project Github Repository Url"),
                sponsor_challenges=sponsors,
            )
        )

    for key, row in sorted(index.items()):
        if key not in matched:
            notes.append(
                Note(
                    row.source, row.line_number,
                    row.text(PROJECT_TITLE_COLUMN), "devpost_only_submission",
                    "in the DevPost export but missing from the project list; "
                    "this team may have no table number",
                )
            )

    out.sort(key=lambda r: r.id)
    return out, notes


# --------------------------------------------------------------------------
# judges
# --------------------------------------------------------------------------


def _name_key(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def build_judges(
    credential_rows: Sequence[SourceRow],
    roster_rows: Sequence[SourceRow],
    *,
    registry: Optional[IdRegistry] = None,
) -> TransformResult:
    """Merge the credentials sheet with the roster into ``JudgeRecord``s.

    The credentials sheet is authoritative for track / email / username /
    check-in; the roster supplies company / role / format. Judges present in
    only one sheet are still emitted, and flagged.

    The ``Password`` column is read but never propagated. Judge passwords belong
    in Firebase Auth, not in a ``judges`` document that the leaderboard page
    reads with a wide-open ``onSnapshot(collection(db, "judges"))``.
    """
    registry = registry if registry is not None else IdRegistry("judge")
    result = TransformResult(rows_in=len(credential_rows) + len(roster_rows))

    roster_by_name: Dict[str, SourceRow] = {}
    for row in sorted(roster_rows, key=lambda r: (r.source, r.line_number)):
        key = _name_key(row.text("Name"))
        if key and key not in roster_by_name:
            roster_by_name[key] = row

    seen_names: Dict[str, int] = {}
    used_roster: Set[str] = set()

    # File order, not caller order -- see build_projects.
    for row in sorted(credential_rows, key=lambda r: (r.source, r.line_number)):
        name = row.text("Name")
        identity = name or "<blank name>"

        if not name:
            # A row with no name, no email and no username is spreadsheet
            # filler -- the check-in sheet has seven of these trailing rows,
            # each holding nothing but Checked-In=FALSE. There is no record
            # there to lose, so this is a note (counted, with line numbers)
            # rather than a rejection that blocks every future --commit. A
            # blank name next to a real email or username IS a rejection: that
            # is a judge whose name went missing.
            if not row.text("Email") and not row.text("Username"):
                result.notes.append(
                    Note(
                        row.source, row.line_number, "<empty row>", "empty_row",
                        "no name, email or username; nothing to load",
                    )
                )
            else:
                result.rejections.append(
                    Rejection(
                        row.source, row.line_number, identity, "missing_name",
                        f"has email={row.text('Email')!r} / "
                        f"username={row.text('Username')!r} but no name",
                    )
                )
            continue

        key = _name_key(name)
        if key in seen_names:
            result.rejections.append(
                Rejection(
                    row.source, row.line_number, identity, "duplicate_judge",
                    f"already seen at line {seen_names[key]}",
                )
            )
            continue
        seen_names[key] = row.line_number

        try:
            kind, track_name = classify_track(row.text("Tracks"))
        except UnknownTrackError as exc:
            result.rejections.append(
                Rejection(row.source, row.line_number, identity, "unknown_track", str(exc))
            )
            continue

        if kind == "empty":
            result.rejections.append(
                Rejection(
                    row.source, row.line_number, identity, "no_track",
                    "a judge with no track gets the wrong rubric",
                )
            )
            continue

        username = row.text("Username")
        email = row.text("Email").lower()
        needs_review = False
        reasons: List[str] = []

        if not username:
            needs_review = True
            reasons.append("no username; judge cannot log in until one is issued")
            result.notes.append(
                Note(row.source, row.line_number, identity, "no_username")
            )

        if not email:
            needs_review = True
            reasons.append("no email; the app looks judges up by email")
            result.notes.append(Note(row.source, row.line_number, identity, "no_email"))
        elif not looks_like_email(email):
            needs_review = True
            reasons.append(f"email {email!r} is not a valid address")
            result.notes.append(
                Note(row.source, row.line_number, identity, "malformed_email", email)
            )

        if kind == "sponsor":
            needs_review = True
            reasons.append(
                f"judges the {track_name!r} sponsor challenge, which has no "
                f"submissions of its own; exclude from room assignment"
            )
            result.notes.append(
                Note(row.source, row.line_number, identity, "sponsor_challenge_judge",
                     str(track_name))
            )

        roster = roster_by_name.get(key)
        if roster is None:
            result.notes.append(
                Note(row.source, row.line_number, identity, "not_on_roster",
                     "checked in but absent from the roster sheet")
            )
        else:
            used_roster.add(key)
            roster_kind, roster_track = (None, None)
            try:
                roster_kind, roster_track = classify_track(roster.text("Tracks"))
            except UnknownTrackError:
                roster_kind = "unknown"
            if roster_kind in ("track", "sponsor") and roster_track != track_name:
                result.notes.append(
                    Note(
                        row.source, row.line_number, identity, "track_disagreement",
                        f"credentials say {track_name!r}, roster says "
                        f"{roster_track!r}; using credentials",
                    )
                )

        doc_id = registry.claim(
            judge_id(username, name), f"{row.where()} {name!r}"
        )

        result.records.append(
            JudgeRecord(
                id=doc_id,
                name=name,
                email=email,
                track=track_name if kind == "track" else None,
                sponsor_challenge=track_name if kind == "sponsor" else None,
                username=username,
                checked_in=row.text("Checked-In").strip().upper() == "TRUE",
                company=roster.text("Company") if roster else "",
                role=roster.text("Role") if roster else "",
                format=roster.text("Format") if roster else "",
                needs_review=needs_review,
                review_reason="; ".join(reasons),
            )
        )

    for key, roster in roster_by_name.items():
        if key not in used_roster:
            result.notes.append(
                Note(
                    roster.source, roster.line_number, roster.text("Name"),
                    "roster_only_judge",
                    "on the roster but not in the credentials sheet; no login "
                    "was issued, so no judge document is written",
                )
            )

    result.records.sort(key=lambda r: r.id)
    return result


__all__ = [
    "IdCollisionError",
    "IdRegistry",
    "JudgeRecord",
    "Note",
    "ProjectRecord",
    "Rejection",
    "SPONSOR_CHALLENGES",
    "TransformError",
    "TransformResult",
    "build_devpost_index",
    "build_judges",
    "build_projects",
    "classify_provenance",
    "classify_track",
    "judge_id",
    "looks_like_email",
    "parse_table_number",
    "project_id",
    "slugify",
]
