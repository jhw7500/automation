### New findings

#### [MEDIUM] Broad ValueError catch hides invalid configuration
- Changed anchor: {"path":"review_cases.py","line":26}
- Trigger evidence: {"path":"review_cases.py","line":25,"quote":"        return int(value)"}
- Impact class: runtime
- Material impact: Invalid numeric configuration is converted into a normal result.

The caller cannot distinguish invalid input from a supported value.
