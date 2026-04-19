export const CRITERIA = [
  {
    id: "innovation",
    label: "Innovation",
    hint: "Novelty of the idea and originality of approach",
    maxScore: 10
  },
  {
    id: "technical",
    label: "Technical Depth",
    hint: "Complexity, code quality, and engineering rigor",
    maxScore: 10
  },
  {
    id: "impact",
    label: "Impact",
    hint: "Potential real-world value and scale of problem addressed",
    maxScore: 10
  },
  {
    id: "execution",
    label: "Execution",
    hint: "Polish, completeness, and quality of the final demo",
    maxScore: 10
  }
];

export const DEFAULT_SCORES = CRITERIA.reduce((acc, criterion) => {
  acc[criterion.id] = null;
  return acc;
}, {});

export const MAX_TOTAL_SCORE = 50;
