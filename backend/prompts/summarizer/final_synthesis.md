You are synthesizing a research paper's structured summary from per-section notes.
The user's active research project is: {active_project_name}.
The user's recent project context: {project_context_short}

Section notes (in order):
---
{joined_intermediates}
---

Produce a structured summary matching the required JSON schema. Rules:

- `title`: use the paper's title if obvious from the notes, otherwise "Unknown title".
- `key_claims`: 3-7 specific claims. Each is a complete sentence. Numbers,
  gene names, drug names, method names — keep them.
- `methods`, `results`, `limitations`: each 2-4 sentences, factual, no hedging
  ("the paper seems to...").
- `relevance_to_user`: tie to the active project explicitly. If no clear
  connection, say "Not directly relevant to {active_project_name}".

Do not invent claims not present in the notes.
