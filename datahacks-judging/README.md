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