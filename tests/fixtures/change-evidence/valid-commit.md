Validate change evidence across authoring tools

### Contract version
v1

### Why
Weekly reports need durable reasons and outcomes instead of opaque short subjects.

### Changes
- Add the v1 Markdown validator.
- Package the validator as a composite action.

### Validation
- `pytest -q tests/test_change_evidence.py` passed.

### References
Issue: #194
