### Contract version
v1

### Context and problem
Weekly reports lose the reason and outcome when only a short Git commit title is available.

### Goal
Make change evidence structurally consistent across authors and repositories.

### Non-goals
Not applicable: this issue does not rewrite historical commits.

### Acceptance criteria
- [ ] The v1 validator accepts every valid fixture.
- [ ] The validator rejects missing evidence with a stable finding code.

### Constraints and impact
The format must remain plain Markdown and require no model-specific prompt.
