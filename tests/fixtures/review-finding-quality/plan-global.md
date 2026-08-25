### New findings

#### [HIGH] plan_global can bypass plan isolation
- Changed anchor: {"path":"review_cases.py","line":12}
- Trigger evidence: {"path":"review_cases.py","line":10,"quote":"    return execute_plan(plan, plan_global=GLOBAL_PLAN)"}
- Impact class: data-integrity
- Material impact: A non-local plan can be executed instead of the validated plan.
