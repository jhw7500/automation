"""릴리즈 픽스처 공용 헬퍼.

test_verify_workflow_release.py 와 test_workflow_release_bundle.py 가 라이브 트리를
역사적 태그 픽스처로 되돌릴 때 공유한다. automation_ref 는 라이브 config 에서 읽어
치환하므로 버전 범프 때 이 파일을 손볼 필요가 없다(하드코딩 범프 체크리스트 제거).
"""

from __future__ import annotations

import json
from pathlib import Path


HISTORICAL_REVIEW_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures/review-workflows-v1.44"
)
HISTORICAL_REVIEW_WORKFLOWS = (
    "claude-code-review.yml",
    "gemini-auto-review.yml",
    "opencode-auto-review.yml",
)
V145_REVIEW_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures/review-workflows-v1.45.2"
)
V145_REVIEW_WORKFLOWS = (
    "claude-code-review.yml",
    "gemini-auto-review.yml",
)
PRE_V146_SETUP_AUTH_FIXTURE = (
    Path(__file__).parent / "fixtures/setup-gemini-auth-v1.45.2.yml"
)
V145_PREPARE_DIFF_FIXTURE = (
    Path(__file__).parent / "fixtures/prepare-review-diff-v1.45.2.yml"
)


LABELED_TRIGGER_TYPES = "    types: [opened, synchronize, ready_for_review, labeled]\n"
PRE_V164_TRIGGER_TYPES = "    types: [opened, synchronize, ready_for_review]\n"
LABELED_GUARD_LINES = (
    "      github.event.pull_request.draft == false &&\n"
    "      (github.event.action != 'labeled' || github.event.label.name == 'review:request')) ||\n"
)
PRE_V164_GUARD_LINES = "      github.event.pull_request.draft == false) ||\n"
OVERRIDE_DESCRIPTION = (
    "        description: Perform one authorized same-HEAD override round; "
    "requires the review-budget-override label\n"
)
PRE_V164_DESCRIPTION = "        description: Perform one authorized same-HEAD review\n"
CATALOG_LABELED_TYPES = '            "synchronize",\n            "ready_for_review",\n            "labeled"\n'
CATALOG_PRE_V164_TYPES = '            "synchronize",\n            "ready_for_review"\n'
CATALOG_OVERRIDE_DESCRIPTION = (
    '"description": "Perform one authorized same-HEAD override round; '
    'requires the review-budget-override label"'
)
CATALOG_PRE_V164_DESCRIPTION = '"description": "Perform one authorized same-HEAD review"'


def restore_pre_v164_label_trigger(repo: Path) -> None:
    """Restore the pre-v1.64 review callers and catalog triggers by text.

    v1.64 subscribes the three managed review callers to `labeled`, guards that
    event on `review:request`, and describes `force_review` as the override round;
    the catalog `trigger` blocks change with them. This runs first (newest release
    first) so the older restores still find the text they assert on.
    """

    # Idempotent: a fixture that already restored (or never had) the v1.64 text is
    # left alone, so this can also run first inside the older restore chains.
    for workflow in ("claude-code-review.yml", "gemini-auto-review.yml", "opencode-auto-review.yml"):
        path = repo / "examples/baseline-workflows/.github/workflows" / workflow
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for current, historical in (
            (LABELED_TRIGGER_TYPES, PRE_V164_TRIGGER_TYPES),
            (LABELED_GUARD_LINES, PRE_V164_GUARD_LINES),
            (OVERRIDE_DESCRIPTION, PRE_V164_DESCRIPTION),
        ):
            # 0 is legitimate: trees older than v1.51 carry neither form and are
            # restored further by the callers below.
            assert text.count(current) <= 1, (workflow, current)
            text = text.replace(current, historical)
        path.write_text(text, encoding="utf-8")
    catalog = repo / "scripts/workflow-catalog.json"
    if catalog.exists():
        text = catalog.read_text(encoding="utf-8")
        for current, historical in (
            (CATALOG_LABELED_TYPES, CATALOG_PRE_V164_TYPES),
            (CATALOG_OVERRIDE_DESCRIPTION, CATALOG_PRE_V164_DESCRIPTION),
        ):
            assert text.count(current) in (0, 3), current
            text = text.replace(current, historical)
        catalog.write_text(text, encoding="utf-8")



def restore_pre_v151_review_policy_callers(repo: Path) -> None:
    """Remove only the v1.51 caller policy surface from historical fixtures."""

    restore_pre_v165_skip_reason_notice(repo)
    restore_pre_v164_label_trigger(repo)
    baseline_root = repo / "examples/baseline-workflows/.github/workflows"
    review_mode = (
        "      review_mode: >-\n"
        "        ${{\n"
        "          github.event_name == 'workflow_dispatch' && inputs.force_review && 'request' ||\n"
        "          contains(github.event.pull_request.labels.*.name, 'review:request') &&\n"
        "          contains(github.event.pull_request.labels.*.name, 'review:skip') && 'conflict' ||\n"
        "          contains(github.event.pull_request.labels.*.name, 'review:request') && 'request' ||\n"
        "          contains(github.event.pull_request.labels.*.name, 'review:skip') && 'skip' ||\n"
        "          'auto'\n"
        "        }}\n"
    )
    draft_guard = (
        "      github.event.pull_request.head.repo.full_name == github.repository &&\n"
        "      github.event.pull_request.draft == false) ||\n"
    )
    historical_head_guard = (
        "      github.event.pull_request.head.repo.full_name == github.repository) ||\n"
    )
    dispatch = (
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      pr_number:\n"
        "        description: Pull request number\n"
        "        type: number\n"
        "        required: true\n"
        "      force_review:\n"
        "        description: Perform one authorized same-HEAD review\n"
        "        type: boolean\n"
        "        required: false\n"
        "        default: false\n"
    )
    forced_if = (
        "    if: >-\n"
        "      (github.event_name == 'pull_request' &&\n"
        "      github.event.pull_request.head.repo.fork == false &&\n"
        "      github.event.pull_request.head.repo.full_name == github.repository) ||\n"
        "      (github.event_name == 'workflow_dispatch' && inputs.force_review)\n"
    )
    for filename in (
        "claude-code-review.yml",
        "gemini-auto-review.yml",
        "opencode-auto-review.yml",
    ):
        path = baseline_root / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        live_trigger_count = text.count(
            "    types: [opened, synchronize, ready_for_review]\n"
        )
        if live_trigger_count == 0:
            assert text.count("    types: [opened, synchronize]\n") == 1
            assert review_mode not in text
            assert draft_guard not in text
            continue
        assert live_trigger_count == 1
        assert text.count(draft_guard) == 1
        assert text.count(review_mode) == 1
        text = text.replace(
            "    types: [opened, synchronize, ready_for_review]\n",
            "    types: [opened, synchronize]\n",
            1,
        ).replace(draft_guard, historical_head_guard, 1).replace(
            review_mode, "", 1
        )
        if filename == "opencode-auto-review.yml":
            assert text.count(dispatch) == 1
            assert text.count(forced_if) == 1
            assert text.count(
                "      force_review: ${{ github.event_name == 'workflow_dispatch' && inputs.force_review }}\n"
            ) == 1
            text = text.replace(dispatch, "", 1).replace(forced_if, "", 1).replace(
                "      pr_number: ${{ github.event.pull_request.number || inputs.pr_number }}\n",
                "      pr_number: ${{ github.event.pull_request.number }}\n",
                1,
            ).replace(
                "      force_review: ${{ github.event_name == 'workflow_dispatch' && inputs.force_review }}\n",
                "",
                1,
            )
        path.write_text(text, encoding="utf-8")

    catalog = repo / "scripts/workflow-catalog.json"
    if catalog.exists():
        text = catalog.read_text(encoding="utf-8")
        ready = '            "synchronize",\n            "ready_for_review"\n'
        review_mode_entry = ',\n            "review_mode"\n'
        ready_count = text.count(ready)
        review_mode_count = text.count(review_mode_entry)
        if ready_count == 0 and review_mode_count == 0:
            return
        assert ready_count == 3
        assert review_mode_count == 3
        text = text.replace(ready, '            "synchronize"\n', 3).replace(
            review_mode_entry, "\n", 3
        )
        opencode_start = text.index(
            '      "path": ".github/workflows/opencode-auto-review.yml"'
        )
        opencode_end = text.index(
            '      "path": ".github/workflow-config.yml"', opencode_start
        )
        opencode = text[opencode_start:opencode_end]
        dispatch_entry = (
            '        },\n'
            '        "workflow_dispatch": {\n'
            '          "inputs": {\n'
            '            "pr_number": {\n'
            '              "description": "Pull request number",\n'
            '              "type": "number",\n'
            '              "required": "true"\n'
            '            },\n'
            '            "force_review": {\n'
            '              "description": "Perform one authorized same-HEAD review",\n'
            '              "type": "boolean",\n'
            '              "required": "false",\n'
            '              "default": "false"\n'
            '            }\n'
            '          }\n'
            '        }\n'
        )
        assert opencode.count(dispatch_entry) == 1
        assert opencode.count('            "force_review",\n') == 1
        opencode = opencode.replace(dispatch_entry, "        }\n", 1).replace(
            '            "force_review",\n', "", 1
        )
        catalog.write_text(
            text[:opencode_start] + opencode + text[opencode_end:],
            encoding="utf-8",
        )


def restore_pre_v151_review_policy(repo: Path) -> None:
    """Restore live reusable workflows and callers to their pre-v1.51 policy."""

    restore_pre_v151_review_policy_callers(repo)
    workflow_root = repo / ".github/workflows"
    review_mode_input = (
        "      review_mode:\n"
        "        description: Resolved PR review policy\n"
        "        type: string\n"
        "        required: false\n"
        "        default: auto\n"
    )
    policy_outputs = (
        "      policy_run: ${{ steps.review_policy.outputs.run-review }}\n"
        "      policy_reason: ${{ steps.review_policy.outputs.reason }}\n"
        "      policy_head: ${{ steps.review_policy.outputs.head-sha }}\n"
    )

    for filename, workflow_name in (
        ("claude-code-review.yml", "claude-code-review"),
        ("gemini-auto-review.yml", "gemini-auto-review"),
    ):
        path = workflow_root / filename
        text = path.read_text(encoding="utf-8")
        pr_number = "${{ inputs.pr_number || github.event.pull_request.number }}"
        policy_step = (
            "      - name: Resolve PR review policy\n"
            "        id: review_policy\n"
            "        uses: $/.github/actions/resolve-review-policy\n"
            "        with:\n"
            f"          workflow-name: {workflow_name}\n"
            f"          pr-number: {pr_number}\n"
            "          review-mode: ${{ inputs.review_mode }}\n"
            "          force-run: ${{ inputs.force_run && 'true' || 'false' }}\n"
            "          force-review: ${{ inputs.force_review && 'true' || 'false' }}\n"
            "          github-token: ${{ github.token }}\n"
        )
        auto_step = (
            "      - name: Check auto review mode\n"
            "        id: auto_mode\n"
            "        env:\n"
            "          FORCE_RUN: ${{ (inputs.force_run || inputs.force_review) && 'true' || 'false' }}\n"
            "        run: |-\n"
            "          CONFIG_FILE=\".github/workflow-config.yml\"\n"
            "          if [[ \"$FORCE_RUN\" == 'true' ]]; then\n"
            "            echo \"auto_enabled=true\" >> \"$GITHUB_OUTPUT\"\n"
            "            exit 0\n"
            "          fi\n"
            "\n"
            "          auto_enabled=\"true\"\n"
            "          if [[ -f \"$CONFIG_FILE\" ]]; then\n"
            "            # Per-workflow auto field: workflows.<name>.auto (default: true)\n"
            "            # Falls back to global review.auto for backward compatibility\n"
            "            auto_enabled=\"$(ruby -ryaml -e '\n"
            "              cfg = (YAML.load_file(ARGV[0]) rescue {}) || {}\n"
            f"              v = cfg.dig(\"workflows\", \"{workflow_name}\", \"auto\")\n"
            "              v = cfg.dig(\"review\", \"auto\") if v.nil?\n"
            "              v = true if v.nil?\n"
            "              puts(v ? \"true\" : \"false\")\n"
            "            ' \"$CONFIG_FILE\" 2>/dev/null || echo true)\"\n"
            "          fi\n"
            "          echo \"auto_enabled=${auto_enabled}\" >> \"$GITHUB_OUTPUT\"\n"
        )
        assert text.count(review_mode_input) == 1
        assert text.count(policy_outputs) == 1
        assert text.count(policy_step) == 1
        assert text.count(
            "          force-run: ${{ inputs.force_run && 'true' || 'false' }}\n"
        ) == 2
        assert text.count("needs.check-enabled.outputs.policy_run") == 2
        text = text.replace(review_mode_input, "", 1).replace(
            policy_outputs,
            "      auto_enabled: ${{ steps.auto_mode.outputs.auto_enabled }}\n",
            1,
        ).replace(
            "          force-run: ${{ inputs.force_run && 'true' || 'false' }}\n",
            "          force-run: ${{ (inputs.force_run || inputs.force_review) && 'true' || 'false' }}\n",
            1,
        ).replace(policy_step, auto_step, 1).replace(
            "needs.check-enabled.outputs.policy_run",
            "needs.check-enabled.outputs.auto_enabled",
            2,
        )
        if filename == "gemini-auto-review.yml":
            permission_block = (
                "    permissions:\n"
                "      contents: read\n"
                "      pull-requests: read\n"
                "    name: Check if enabled\n"
            )
            assert text.count(permission_block) == 1
            text = text.replace(
                permission_block,
                "    permissions:\n      contents: read\n    name: Check if enabled\n",
                1,
            )
        path.write_text(text, encoding="utf-8")

    path = workflow_root / "opencode-auto-review.yml"
    text = path.read_text(encoding="utf-8")
    force_review_input = (
        "      force_review:\n"
        "        description: Perform one explicitly authorized review even when HEAD is unchanged\n"
        "        type: boolean\n"
        "        required: false\n"
        "        default: false\n"
    )
    policy_step = (
        "      - name: Resolve PR review policy\n"
        "        id: review_policy\n"
        "        uses: $/.github/actions/resolve-review-policy\n"
        "        with:\n"
        "          workflow-name: opencode-auto-review\n"
        "          pr-number: ${{ inputs.pr_number || github.event.pull_request.number || github.event.issue.number }}\n"
        "          review-mode: ${{ inputs.review_mode }}\n"
        "          force-run: ${{ inputs.force_run && 'true' || 'false' }}\n"
        "          force-review: ${{ inputs.force_review && 'true' || 'false' }}\n"
        "          github-token: ${{ github.token }}\n"
    )
    historical_steps = (
        "      - name: Check auto review mode\n"
        "        id: auto_mode\n"
        "        env:\n"
        "          FORCE_RUN: ${{ inputs.force_run && 'true' || 'false' }}\n"
        "        run: |-\n"
        "          CONFIG_FILE=\".github/workflow-config.yml\"\n"
        "          if [[ \"$FORCE_RUN\" == 'true' ]]; then\n"
        "            echo \"auto_enabled=true\" >> \"$GITHUB_OUTPUT\"\n"
        "            exit 0\n"
        "          fi\n"
        "\n"
        "          auto_enabled=\"true\"\n"
        "          if [[ -f \"$CONFIG_FILE\" ]]; then\n"
        "            # Precedence (matches claude/gemini): workflows.opencode-auto-review.auto → review.auto → default true\n"
        "            auto_enabled=\"$(ruby -ryaml -e 'cfg = (YAML.load_file(ARGV[0]) rescue {}) || {}; v = cfg.dig(\"workflows\", \"opencode-auto-review\", \"auto\"); v = cfg.dig(\"review\", \"auto\") if v.nil?; v = true if v.nil?; puts(v ? \"true\" : \"false\")' \"$CONFIG_FILE\" 2>/dev/null || echo true)\"\n"
        "          fi\n"
        "          echo \"auto_enabled=${auto_enabled}\" >> \"$GITHUB_OUTPUT\"\n"
        "\n"
        "      - name: Verify same-repository PR\n"
        "        id: pr_scope\n"
        "        env:\n"
        "          GH_TOKEN: ${{ github.token }}\n"
        "          PR_NUMBER: ${{ inputs.pr_number || github.event.pull_request.number || github.event.issue.number }}\n"
        "        run: |\n"
        "          echo \"safe_pr=false\" >> \"$GITHUB_OUTPUT\"\n"
        "          if [[ ! \"$PR_NUMBER\" =~ ^[0-9]+$ ]]; then\n"
        "            exit 0\n"
        "          fi\n"
        "          metadata=\"$(gh api \"repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}\" \\\n"
        "            --jq '[.head.repo.full_name, .head.repo.fork] | @tsv' 2>/dev/null)\" || exit 0\n"
        "          IFS=$'\\t' read -r head_repo head_fork <<< \"$metadata\"\n"
        "          if [[ \"$head_repo\" == \"$GITHUB_REPOSITORY\" && \"$head_fork\" == \"false\" ]]; then\n"
        "            echo \"safe_pr=true\" >> \"$GITHUB_OUTPUT\"\n"
        "          fi\n"
    )
    assert text.count(review_mode_input) == 1
    assert text.count(force_review_input) == 1
    assert text.count(policy_outputs) == 1
    assert text.count(policy_step) == 1
    assert text.count("needs.check-enabled.outputs.policy_run == 'true'") == 3
    assert text.count("needs.check-enabled.outputs.policy_run != 'true'") == 1
    text = text.replace(review_mode_input, "", 1).replace(
        force_review_input, "", 1
    ).replace(
        policy_outputs,
        "      auto_enabled: ${{ steps.auto_mode.outputs.auto_enabled }}\n"
        "      safe_pr: ${{ steps.pr_scope.outputs.safe_pr }}\n",
        1,
    ).replace(policy_step, historical_steps, 1).replace(
        "needs.check-enabled.outputs.policy_run == 'true'",
        "needs.check-enabled.outputs.auto_enabled == 'true' &&\n"
        "      needs.check-enabled.outputs.safe_pr == 'true'",
        3,
    ).replace(
        "needs.check-enabled.outputs.policy_run != 'true'",
        "needs.check-enabled.outputs.auto_enabled != 'true' ||\n"
        "      needs.check-enabled.outputs.safe_pr != 'true'",
        1,
    )
    for line in (
        "          force-full: ${{ inputs.force_review && 'true' || 'false' }}\n",
        "          force-review: ${{ inputs.force_review && 'true' || 'false' }}\n",
    ):
        assert text.count(line) == 1
        text = text.replace(line, "", 1)
    force_claim = (
        "      - name: Enforce force-review claim\n"
        "        if: ${{ always() && !cancelled() && inputs.force_review && steps.review-budget-claim.outputs.allow-invocation != 'true' }}\n"
        "        run: |\n"
        "          echo '::error::force-review was not authorized by the bounded review budget'\n"
        "          exit 1\n"
        "\n"
    )
    assert text.count(force_claim) == 1
    path.write_text(text.replace(force_claim, "", 1), encoding="utf-8")


def restore_pre_force_review_callers(repo: Path) -> None:
    """Remove the force-review caller surface from historical fixtures."""

    restore_pre_v151_review_policy_callers(repo)

    baseline_root = repo / "examples/baseline-workflows/.github/workflows"
    for filename in ("claude-code-review.yml", "gemini-auto-review.yml"):
        path = baseline_root / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        dispatch = (
            "  workflow_dispatch:\n"
            "    inputs:\n"
            "      pr_number:\n"
            "        description: Pull request number\n"
            "        type: number\n"
            "        required: true\n"
            "      force_review:\n"
            "        description: Perform one authorized same-HEAD review\n"
            "        type: boolean\n"
            "        required: false\n"
            "        default: false\n"
        )
        forced_if = (
            "    if: >-\n"
            "      (github.event_name == 'pull_request' &&\n"
            "      github.event.pull_request.head.repo.fork == false &&\n"
            "      github.event.pull_request.head.repo.full_name == github.repository) ||\n"
            "      (github.event_name == 'workflow_dispatch' && inputs.force_review)\n"
        )
        assert text.count(dispatch) == 1
        assert text.count(forced_if) == 1
        assert text.count("      force_review: ${{ github.event_name == 'workflow_dispatch' && inputs.force_review }}\n") == 1
        text = text.replace(dispatch, "", 1).replace(
            forced_if,
            "    if: ${{ github.event.pull_request.head.repo.fork == false && github.event.pull_request.head.repo.full_name == github.repository }}\n",
            1,
        ).replace(
            "      pr_number: ${{ github.event.pull_request.number || inputs.pr_number }}\n",
            "      pr_number: ${{ github.event.pull_request.number }}\n",
            1,
        ).replace(
            "      force_review: ${{ github.event_name == 'workflow_dispatch' && inputs.force_review }}\n",
            "",
            1,
        )
        path.write_text(text, encoding="utf-8")

    catalog = repo / "scripts/workflow-catalog.json"
    if catalog.exists():
        text = catalog.read_text(encoding="utf-8")
        dispatch = (
            '        },\n'
            '        "workflow_dispatch": {\n'
            '          "inputs": {\n'
            '            "pr_number": {\n'
            '              "description": "Pull request number",\n'
            '              "type": "number",\n'
            '              "required": "true"\n'
            '            },\n'
            '            "force_review": {\n'
            '              "description": "Perform one authorized same-HEAD review",\n'
            '              "type": "boolean",\n'
            '              "required": "false",\n'
            '              "default": "false"\n'
            '            }\n'
            '          }\n'
            '        }\n'
        )
        assert text.count(dispatch) == 2
        assert text.count('            "force_review",\n') == 2
        text = text.replace(dispatch, "        }\n", 2).replace(
            '            "force_review",\n', "", 2
        )
        catalog.write_text(text, encoding="utf-8")


def restore_pre_v146_review_contracts(repo: Path) -> None:
    """Restore shared auth identity and Claude/Gemini caller ceilings."""

    restore_pre_force_review_callers(repo)
    setup = repo / ".github/actions/setup-gemini-auth/action.yml"
    setup.parent.mkdir(parents=True, exist_ok=True)
    setup.write_bytes(PRE_V146_SETUP_AUTH_FIXTURE.read_bytes())

    baseline_root = repo / "examples/baseline-workflows/.github/workflows"
    for filename in ("claude-code-review.yml", "gemini-auto-review.yml"):
        path = baseline_root / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        needle = "    permissions:\n      actions: read\n"
        assert text.count(needle) == 1
        text = text.replace(needle, "    permissions:\n", 1)
        if filename == "gemini-auto-review.yml":
            publisher_input = "      publisher_app_id: ${{ vars.APP_ID }}\n"
            assert text.count(publisher_input) == 1
            text = text.replace(publisher_input, "", 1)
        path.write_text(text, encoding="utf-8")

    catalog = repo / "scripts/workflow-catalog.json"
    if catalog.exists():
        text = catalog.read_text(encoding="utf-8")
        for caller in ("claude-review", "gemini-review"):
            needle = (
                f'          "name": "{caller}",\n'
                '          "permissions": {\n'
                '            "actions": "read",\n'
            )
            replacement = (
                f'          "name": "{caller}",\n          "permissions": {{\n'
            )
            assert text.count(needle) == 1
            text = text.replace(needle, replacement, 1)
        publisher_input = '            "publisher_app_id",\n'
        assert text.count(publisher_input) == 1
        text = text.replace(publisher_input, "", 1)
        catalog.write_text(text, encoding="utf-8")


def restore_historical_automation_ref(repo: Path, historical_ref: str) -> None:
    """픽스처 트리의 automation_ref 를 라이브 값에서 역사적 값으로 되돌린다."""
    config_path = repo / "scripts/workflow-config.json"
    config_text = config_path.read_text(encoding="utf-8")
    live_ref = json.loads(config_text)["automation_ref"]
    needle = f'"automation_ref": "{live_ref}"'
    assert config_text.count(needle) == 1, (
        f"workflow-config.json 에서 {needle} 를 정확히 1회 찾지 못했습니다 "
        f"(count={config_text.count(needle)}) — config 포맷이 바뀌면 이 헬퍼를 갱신하세요"
    )
    config_path.write_text(
        config_text.replace(needle, f'"automation_ref": "{historical_ref}"', 1),
        encoding="utf-8",
    )


def restore_historical_review_workflows(
    repo: Path,
    filenames: tuple[str, ...] = HISTORICAL_REVIEW_WORKFLOWS,
) -> None:
    """Restore genuine v1.44 central review bytes into a historical fixture.

    The snapshots come from immutable v1.44 commit 5ec427c540619d6fbd80ea758de8d8e0bf00d987.
    Keeping them in the test tree makes historical fixtures independent of Git history.
    """

    for filename in filenames:
        relative = f".github/workflows/{filename}"
        (repo / relative).write_bytes(
            (HISTORICAL_REVIEW_FIXTURE_ROOT / filename).read_bytes()
        )

    restore_pre_v146_review_contracts(repo)

    # v1.44 callers predate the Task 5 Actions/Checks ceiling. Restore those two
    # release-policy files without altering the immutable central workflow fixtures.
    baseline = repo / "examples/baseline-workflows/.github/workflows/opencode-auto-review.yml"
    if baseline.exists():
        text = baseline.read_text(encoding="utf-8")
        text = text.replace("      actions: read\n      checks: write\n", "", 1)
        baseline.write_text(text, encoding="utf-8")
    catalog = repo / "scripts/workflow-catalog.json"
    if catalog.exists():
        text = catalog.read_text(encoding="utf-8")
        text = text.replace(
            '          "permissions": {\n'
            '            "actions": "read",\n'
            '            "checks": "write",\n'
            '            "contents": "read",\n'
            '            "issues": "write",\n'
            '            "pull-requests": "write"\n'
            "          },",
            '          "permissions": {\n'
            '            "contents": "read",\n'
            '            "issues": "write",\n'
            '            "pull-requests": "write"\n'
            "          },",
            1,
        )
        catalog.write_text(text, encoding="utf-8")


def restore_v145_review_workflows(repo: Path) -> None:
    """Restore immutable v1.45.2 Claude/Gemini workflow bytes.

    These fixtures are the exact blobs published by commit
    abf5e65cf6188277d9984be062d0b069c82cf25f.  Reading only checked-in
    fixtures keeps historical verification available in shallow repositories.
    """

    for filename in V145_REVIEW_WORKFLOWS:
        relative = f".github/workflows/{filename}"
        (repo / relative).write_bytes(
            (V145_REVIEW_FIXTURE_ROOT / filename).read_bytes()
        )

    restore_pre_v146_review_contracts(repo)
    prepare = repo / ".github/actions/prepare-review-diff/action.yml"
    prepare.parent.mkdir(parents=True, exist_ok=True)
    prepare.write_bytes(V145_PREPARE_DIFF_FIXTURE.read_bytes())


PRE_V165_SKIPPED_JOBS = {
    'claude-code-review.yml': (
        '  skipped:\n    name: Workflow Skipped\n    needs: check-enabled\n    if: needs.check-enabled.outputs.enabled != \'true\' || needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        # 이 잡은 두 가지 이유로 돌 수 있으므로 하나로 단정하지 않는다. enabled 는\n        # workflow-config.yml 이 끈 경우이고, policy_reason 은 리졸버가 내는 고정\n        # 어휘라 비신뢰 텍스트가 아니다. 정규식이 어휘 밖 값을 막고, 매핑에 없는\n        # 값은 사유를 그대로 보여 준다.\n        env:\n          WORKFLOW_ENABLED: ${{ needs.check-enabled.outputs.enabled }}\n          POLICY_REASON: ${{ needs.check-enabled.outputs.policy_reason }}\n        run: |\n          set -euo pipefail\n          [[ "$POLICY_REASON" =~ ^[a-z_]*$ ]]\n          if [[ "$WORKFLOW_ENABLED" != "true" ]]; then\n            detail="claude-code-review is disabled in .github/workflow-config.yml"\n          else\n            case "$POLICY_REASON" in\n              default_auto_false|workflow_auto_false|review_auto_false)\n                detail="automatic review is off for this repository; add the review:request label to run one" ;;\n              skip) detail="the review:skip label is present" ;;\n              draft) detail="the pull request is a draft" ;;\n              closed) detail="the pull request is not open" ;;\n              unsafe_pr) detail="the pull request head is not in this repository" ;;\n              workflow_disabled) detail="claude-code-review is disabled in .github/workflow-config.yml" ;;\n              "") detail="the review policy produced no reason; see the Check if enabled job" ;;\n              *) detail="the review policy declined with reason $POLICY_REASON" ;;\n            esac\n          fi\n          echo "::notice::Claude Code Review did not run because $detail"\n          {\n            printf \'## Workflow Skipped\\n\'\n            printf \'Claude Code Review did not run because %s.\\n\' "$detail"\n          } >> "$GITHUB_STEP_SUMMARY"\n',
        '  skipped:\n    name: Workflow Skipped\n    needs: check-enabled\n    if: needs.check-enabled.outputs.enabled != \'true\' || needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        run: |\n          echo "::notice::Claude Code Review workflow is disabled in .github/workflow-config.yml"\n          echo "## Workflow Skipped" >> $GITHUB_STEP_SUMMARY\n          echo "Claude Code Review is disabled in workflow-config.yml" >> $GITHUB_STEP_SUMMARY\n',
    ),
    'gemini-auto-review.yml': (
        '  skipped:\n    permissions: {}\n    name: Workflow Skipped\n    needs: check-enabled\n    if: needs.check-enabled.outputs.enabled != \'true\' || needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        # 이 잡은 두 가지 이유로 돌 수 있으므로 하나로 단정하지 않는다. enabled 는\n        # workflow-config.yml 이 끈 경우이고, policy_reason 은 리졸버가 내는 고정\n        # 어휘라 비신뢰 텍스트가 아니다. 정규식이 어휘 밖 값을 막고, 매핑에 없는\n        # 값은 사유를 그대로 보여 준다.\n        env:\n          WORKFLOW_ENABLED: ${{ needs.check-enabled.outputs.enabled }}\n          POLICY_REASON: ${{ needs.check-enabled.outputs.policy_reason }}\n        run: |\n          set -euo pipefail\n          [[ "$POLICY_REASON" =~ ^[a-z_]*$ ]]\n          if [[ "$WORKFLOW_ENABLED" != "true" ]]; then\n            detail="gemini-auto-review is disabled in .github/workflow-config.yml"\n          else\n            case "$POLICY_REASON" in\n              default_auto_false|workflow_auto_false|review_auto_false)\n                detail="automatic review is off for this repository; add the review:request label to run one" ;;\n              skip) detail="the review:skip label is present" ;;\n              draft) detail="the pull request is a draft" ;;\n              closed) detail="the pull request is not open" ;;\n              unsafe_pr) detail="the pull request head is not in this repository" ;;\n              workflow_disabled) detail="gemini-auto-review is disabled in .github/workflow-config.yml" ;;\n              "") detail="the review policy produced no reason; see the Check if enabled job" ;;\n              *) detail="the review policy declined with reason $POLICY_REASON" ;;\n            esac\n          fi\n          echo "::notice::Gemini Auto PR Review did not run because $detail"\n          {\n            printf \'## Workflow Skipped\\n\'\n            printf \'Gemini Auto PR Review did not run because %s.\\n\' "$detail"\n          } >> "$GITHUB_STEP_SUMMARY"\n',
        '  skipped:\n    permissions: {}\n    name: Workflow Skipped\n    needs: check-enabled\n    if: needs.check-enabled.outputs.enabled != \'true\' || needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        run: |\n          echo "::notice::Gemini Auto PR Review workflow is disabled in .github/workflow-config.yml"\n          echo "## Workflow Skipped" >> $GITHUB_STEP_SUMMARY\n          echo "Gemini Auto PR Review is disabled in workflow-config.yml" >> $GITHUB_STEP_SUMMARY\n',
    ),
}


def restore_pre_v165_skip_reason_notice(repo: Path) -> None:
    """Restore the pre-v1.65 skipped notices in the two managed review workflows.

    v1.65 makes the skipped job name the reason the review declined instead of
    always blaming workflow-config.yml. This runs first (newest release first) so
    the older restores still find the text they assert on.
    """

    # Idempotent: a tree that already restored (or never had) the v1.65 job is
    # left alone, so this can also run first inside the older restore chains.
    for workflow, (current, historical) in PRE_V165_SKIPPED_JOBS.items():
        path = repo / ".github/workflows" / workflow
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert text.count(current) <= 1, workflow
        path.write_text(text.replace(current, historical), encoding="utf-8")
