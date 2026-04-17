# datahacks-judging-platform

```
judges: {
  judgeId: {
    name: "Ansh",
    email: "...",
    track: "AI/ML",
    assignedProjects: ["proj1", "proj2", ...]
  }
}
```

```
projects: {
  projectId: {
    name: "EcoML Predictor",
    track: "AI/ML",
    tableNumber: 12
  }
}
```

```
evaluations: {
  autoId: {
    judgeId: "...",
    projectId: "...",
    scores: {
      innovation: 4,
      technical: 5,
      impact: 3,
      execution: 4
    },
    comment: "...",
    timestamp: ...
  }
}
```

## Assignment Sync Workflow

Use a local JSON file as the source of truth for judge-to-project assignments.

1. Copy `assignments.template.json` to `assignments.local.json`
2. Fill in each judge's Firebase Auth `id` and `assignedProjects`
3. Run `npm run sync:assignments`

The sync script will:

- update `judges/{id}.assignedProjects`
- derive and update `projects/{projectId}.assignedJudges`
- fail fast if your JSON references a project id that does not exist in Firestore
