### Contract version
v1

### Summary
Adds deterministic validation for structured change evidence.

### Changes
- Define the v1 headings and field rules.
- Add a dependency-free validator and reusable action.

### Validation
- `pytest -q tests/test_change_evidence.py` passed.

### Impact and risks
Repositories must adopt the template before enabling enforcement.

### Related issue
Closes #194
