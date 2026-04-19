import rubrics from "../assets/rubrics.json";

// Map Firestore track names → JSON track names
const TRACK_ALIASES = {
  "AI/ML":                             "Machine Learning / AI",
  "Analytics":                         "Data Analytics",
  "UI/UX & Web Dev":                   "UI/UX",
  "Cloud":                             "Cloud",
  "Hardware & IoT":                    "Hardware & IoT",
  "Mechanical Design & Biotechnology": "Mechanical Design & Biotechnology",
  "Economics":                         "Economics",
};

function slugify(str) {
  return str.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
}

// Build criteria array from a rubrics.json track entry
function buildCriteria(track) {
  return track.categories.map(c => ({
    id: slugify(c.name),
    label: c.name,
    hint: c.description || "",
    maxScore: c.points,
  }));
}

// Index rubrics by JSON track name
const BY_NAME = {};
for (const t of rubrics.tracks) {
  BY_NAME[t.name] = t;
}

// Build score guide from rubrics.json global scoring_guidance
export const SCORE_GUIDE = Object.entries(rubrics.scoring_guidance).map(([range, label]) => ({
  range,
  label,
  description: label,
}));

// Generic fallback (5 criteria × 10)
const FALLBACK_CRITERIA = [
  { id: "innovation",  label: "Innovation",       hint: "Novelty and originality of approach",                  maxScore: 10 },
  { id: "technical",   label: "Technical Depth",  hint: "Complexity, code quality, and engineering rigor",       maxScore: 10 },
  { id: "impact",      label: "Impact",           hint: "Potential real-world value and scale",                  maxScore: 10 },
  { id: "execution",   label: "Execution",        hint: "Polish, completeness, and quality of the final demo",   maxScore: 10 },
  { id: "theme",       label: "Theme Alignment",  hint: "How well the project addresses the sustainability theme", maxScore: 10 },
];

export function getCriteriaForTrack(track) {
  if (!track) return FALLBACK_CRITERIA;
  const jsonName = TRACK_ALIASES[track] ?? track;
  const entry = BY_NAME[jsonName];
  if (!entry) return FALLBACK_CRITERIA;
  return buildCriteria(entry);
}

export function getPerformanceNoteForTrack(track) {
  const jsonName = TRACK_ALIASES[track] ?? track;
  const entry = BY_NAME[jsonName];
  return entry?.performance_scoring ?? null;
}

export function getDefaultScores(criteria) {
  return Object.fromEntries(criteria.map(c => [c.id, null]));
}

export function getMaxTotal(criteria) {
  return criteria.reduce((s, c) => s + c.maxScore, 0);
}
