### New findings

#### [HIGH] Rejected plan is rendered as successful completion
- Changed anchor: {"path":"review_cases.py","line":20}
- Trigger evidence: {"path":"review_cases.py","line":20,"quote":"        return \"completed 0/{}\".format(total)"}
- Impact class: user-visible
- Material impact: A rejected plan is displayed as a successful zero-item completion.
