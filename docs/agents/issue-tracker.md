# Issue tracker: Local Markdown

Issues and specifications for this repository live as Markdown files under
`.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The specification is `.scratch/<feature-slug>/spec.md`.
- Implementation issues are stored one per file under
  `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`.
- Triage state is recorded as a `Status:` line near the top of each issue file.
- Comments and conversation history are appended under a `## Comments`
  heading.

## Skill operations

- To publish an issue, create the corresponding file under the feature's
  `.scratch/` directory.
- To fetch a ticket, read the path or issue number supplied by the user.
- Do not combine multiple implementation tickets into one file.

## Wayfinding

- Map: `.scratch/<effort>/map.md`
- Child ticket: `.scratch/<effort>/issues/<NN>-<slug>.md`
- Ticket type: a `Type:` field such as `research`, `prototype`, `grilling`, or
  `task`
- Ticket state: a `Status:` field such as `claimed` or `resolved`
- Dependencies: a `Blocked by: NN, NN` field

Claim a ticket by setting `Status: claimed` before work. Resolve it by appending
the result under `## Answer`, setting `Status: resolved`, and adding a concise
context pointer to the map.
