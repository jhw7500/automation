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

    restore_retired_manual_pr_review(repo)
    restore_pre_v166_label_mismatch_decline(repo)
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


PRE_V166_SKIPPED_JOBS = {
    'claude-code-review.yml': (
        '  skipped:\n    name: Workflow Skipped\n    needs: check-enabled\n    if: needs.check-enabled.outputs.enabled != \'true\' || needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        # 이 잡은 두 가지 이유로 돌 수 있으므로 하나로 단정하지 않는다. enabled 는\n        # workflow-config.yml 이 끈 경우이고, policy_reason 은 리졸버가 내는 고정\n        # 어휘라 비신뢰 텍스트가 아니다. 정규식이 어휘 밖 값을 막고, 매핑에 없는\n        # 값은 사유를 그대로 보여 준다.\n        env:\n          WORKFLOW_ENABLED: ${{ needs.check-enabled.outputs.enabled }}\n          POLICY_REASON: ${{ needs.check-enabled.outputs.policy_reason }}\n        run: |\n          set -euo pipefail\n          [[ "$POLICY_REASON" =~ ^[a-z_]*$ ]]\n          if [[ "$WORKFLOW_ENABLED" != "true" ]]; then\n            detail="claude-code-review is disabled in .github/workflow-config.yml"\n          else\n            case "$POLICY_REASON" in\n              default_auto_false|workflow_auto_false|review_auto_false)\n                detail="automatic review is off for this repository; add the review:request label to run one" ;;\n              skip) detail="the review:skip label is present" ;;\n              draft) detail="the pull request is a draft" ;;\n              closed) detail="the pull request is not open" ;;\n              unsafe_pr) detail="the pull request head is not in this repository" ;;\n              workflow_disabled) detail="claude-code-review is disabled in .github/workflow-config.yml" ;;\n              review_mode_label_mismatch)\n                detail="the review:request or review:skip label changed after this run was triggered; the run started by that label carries the verdict" ;;\n              "") detail="the review policy produced no reason; see the Check if enabled job" ;;\n              *) detail="the review policy declined with reason $POLICY_REASON" ;;\n            esac\n          fi\n          echo "::notice::Claude Code Review did not run because $detail"\n          {\n            printf \'## Workflow Skipped\\n\'\n            printf \'Claude Code Review did not run because %s.\\n\' "$detail"\n          } >> "$GITHUB_STEP_SUMMARY"\n',
        '  skipped:\n    name: Workflow Skipped\n    needs: check-enabled\n    if: needs.check-enabled.outputs.enabled != \'true\' || needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        # 이 잡은 두 가지 이유로 돌 수 있으므로 하나로 단정하지 않는다. enabled 는\n        # workflow-config.yml 이 끈 경우이고, policy_reason 은 리졸버가 내는 고정\n        # 어휘라 비신뢰 텍스트가 아니다. 정규식이 어휘 밖 값을 막고, 매핑에 없는\n        # 값은 사유를 그대로 보여 준다.\n        env:\n          WORKFLOW_ENABLED: ${{ needs.check-enabled.outputs.enabled }}\n          POLICY_REASON: ${{ needs.check-enabled.outputs.policy_reason }}\n        run: |\n          set -euo pipefail\n          [[ "$POLICY_REASON" =~ ^[a-z_]*$ ]]\n          if [[ "$WORKFLOW_ENABLED" != "true" ]]; then\n            detail="claude-code-review is disabled in .github/workflow-config.yml"\n          else\n            case "$POLICY_REASON" in\n              default_auto_false|workflow_auto_false|review_auto_false)\n                detail="automatic review is off for this repository; add the review:request label to run one" ;;\n              skip) detail="the review:skip label is present" ;;\n              draft) detail="the pull request is a draft" ;;\n              closed) detail="the pull request is not open" ;;\n              unsafe_pr) detail="the pull request head is not in this repository" ;;\n              workflow_disabled) detail="claude-code-review is disabled in .github/workflow-config.yml" ;;\n              "") detail="the review policy produced no reason; see the Check if enabled job" ;;\n              *) detail="the review policy declined with reason $POLICY_REASON" ;;\n            esac\n          fi\n          echo "::notice::Claude Code Review did not run because $detail"\n          {\n            printf \'## Workflow Skipped\\n\'\n            printf \'Claude Code Review did not run because %s.\\n\' "$detail"\n          } >> "$GITHUB_STEP_SUMMARY"\n',
    ),
    'gemini-auto-review.yml': (
        '  skipped:\n    permissions: {}\n    name: Workflow Skipped\n    needs: check-enabled\n    if: needs.check-enabled.outputs.enabled != \'true\' || needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        # 이 잡은 두 가지 이유로 돌 수 있으므로 하나로 단정하지 않는다. enabled 는\n        # workflow-config.yml 이 끈 경우이고, policy_reason 은 리졸버가 내는 고정\n        # 어휘라 비신뢰 텍스트가 아니다. 정규식이 어휘 밖 값을 막고, 매핑에 없는\n        # 값은 사유를 그대로 보여 준다.\n        env:\n          WORKFLOW_ENABLED: ${{ needs.check-enabled.outputs.enabled }}\n          POLICY_REASON: ${{ needs.check-enabled.outputs.policy_reason }}\n        run: |\n          set -euo pipefail\n          [[ "$POLICY_REASON" =~ ^[a-z_]*$ ]]\n          if [[ "$WORKFLOW_ENABLED" != "true" ]]; then\n            detail="gemini-auto-review is disabled in .github/workflow-config.yml"\n          else\n            case "$POLICY_REASON" in\n              default_auto_false|workflow_auto_false|review_auto_false)\n                detail="automatic review is off for this repository; add the review:request label to run one" ;;\n              skip) detail="the review:skip label is present" ;;\n              draft) detail="the pull request is a draft" ;;\n              closed) detail="the pull request is not open" ;;\n              unsafe_pr) detail="the pull request head is not in this repository" ;;\n              workflow_disabled) detail="gemini-auto-review is disabled in .github/workflow-config.yml" ;;\n              review_mode_label_mismatch)\n                detail="the review:request or review:skip label changed after this run was triggered; the run started by that label carries the verdict" ;;\n              "") detail="the review policy produced no reason; see the Check if enabled job" ;;\n              *) detail="the review policy declined with reason $POLICY_REASON" ;;\n            esac\n          fi\n          echo "::notice::Gemini Auto PR Review did not run because $detail"\n          {\n            printf \'## Workflow Skipped\\n\'\n            printf \'Gemini Auto PR Review did not run because %s.\\n\' "$detail"\n          } >> "$GITHUB_STEP_SUMMARY"\n',
        '  skipped:\n    permissions: {}\n    name: Workflow Skipped\n    needs: check-enabled\n    if: needs.check-enabled.outputs.enabled != \'true\' || needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        # 이 잡은 두 가지 이유로 돌 수 있으므로 하나로 단정하지 않는다. enabled 는\n        # workflow-config.yml 이 끈 경우이고, policy_reason 은 리졸버가 내는 고정\n        # 어휘라 비신뢰 텍스트가 아니다. 정규식이 어휘 밖 값을 막고, 매핑에 없는\n        # 값은 사유를 그대로 보여 준다.\n        env:\n          WORKFLOW_ENABLED: ${{ needs.check-enabled.outputs.enabled }}\n          POLICY_REASON: ${{ needs.check-enabled.outputs.policy_reason }}\n        run: |\n          set -euo pipefail\n          [[ "$POLICY_REASON" =~ ^[a-z_]*$ ]]\n          if [[ "$WORKFLOW_ENABLED" != "true" ]]; then\n            detail="gemini-auto-review is disabled in .github/workflow-config.yml"\n          else\n            case "$POLICY_REASON" in\n              default_auto_false|workflow_auto_false|review_auto_false)\n                detail="automatic review is off for this repository; add the review:request label to run one" ;;\n              skip) detail="the review:skip label is present" ;;\n              draft) detail="the pull request is a draft" ;;\n              closed) detail="the pull request is not open" ;;\n              unsafe_pr) detail="the pull request head is not in this repository" ;;\n              workflow_disabled) detail="gemini-auto-review is disabled in .github/workflow-config.yml" ;;\n              "") detail="the review policy produced no reason; see the Check if enabled job" ;;\n              *) detail="the review policy declined with reason $POLICY_REASON" ;;\n            esac\n          fi\n          echo "::notice::Gemini Auto PR Review did not run because $detail"\n          {\n            printf \'## Workflow Skipped\\n\'\n            printf \'Gemini Auto PR Review did not run because %s.\\n\' "$detail"\n          } >> "$GITHUB_STEP_SUMMARY"\n',
    ),
    'opencode-auto-review.yml': (
        '  skipped:\n    name: Workflow Skipped\n    needs: check-enabled\n    if: >-\n      needs.check-enabled.outputs.enabled != \'true\' ||\n      needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        # 이 잡은 두 가지 이유로 돌 수 있으므로 하나로 단정하지 않는다. enabled 는\n        # workflow-config.yml 이 끈 경우이고, policy_reason 은 리졸버가 내는 고정\n        # 어휘라 비신뢰 텍스트가 아니다. 정규식이 어휘 밖 값을 막고, 매핑에 없는\n        # 값은 사유를 그대로 보여 준다.\n        env:\n          WORKFLOW_ENABLED: ${{ needs.check-enabled.outputs.enabled }}\n          POLICY_REASON: ${{ needs.check-enabled.outputs.policy_reason }}\n        run: |\n          set -euo pipefail\n          [[ "$POLICY_REASON" =~ ^[a-z_]*$ ]]\n          if [[ "$WORKFLOW_ENABLED" != "true" ]]; then\n            detail="opencode-auto-review is disabled in .github/workflow-config.yml"\n          else\n            case "$POLICY_REASON" in\n              default_auto_false|workflow_auto_false|review_auto_false)\n                detail="automatic review is off for this repository; add the review:request label to run one" ;;\n              skip) detail="the review:skip label is present" ;;\n              draft) detail="the pull request is a draft" ;;\n              closed) detail="the pull request is not open" ;;\n              unsafe_pr) detail="the pull request head is not in this repository" ;;\n              workflow_disabled) detail="opencode-auto-review is disabled in .github/workflow-config.yml" ;;\n              review_mode_label_mismatch)\n                detail="the review:request or review:skip label changed after this run was triggered; the run started by that label carries the verdict" ;;\n              "") detail="the review policy produced no reason; see the Check if enabled job" ;;\n              *) detail="the review policy declined with reason $POLICY_REASON" ;;\n            esac\n          fi\n          echo "::notice::OpenCode Auto PR Review did not run because $detail"\n          {\n            printf \'## Workflow Skipped\\n\'\n            printf \'OpenCode Auto PR Review did not run because %s.\\n\' "$detail"\n          } >> "$GITHUB_STEP_SUMMARY"\n',
        '  skipped:\n    name: Workflow Skipped\n    needs: check-enabled\n    if: >-\n      needs.check-enabled.outputs.enabled != \'true\' ||\n      needs.check-enabled.outputs.policy_run != \'true\'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Notice\n        run: |\n          echo "::notice::OpenCode Auto PR Review is disabled or the PR is not from this repository"\n          echo "## Workflow Skipped" >> $GITHUB_STEP_SUMMARY\n          echo "opencode-auto-review is disabled, auto review is off, or the PR is external" >> $GITHUB_STEP_SUMMARY\n',
    ),
}

PRE_V166_MISMATCH_HUNK = (
    '    if request.review_mode != label_mode and not (\n        manual_request and label_mode == "auto"\n    ):\n        # A label that moved between the trigger and this read declines the run.\n        # No review happens under either outcome, so failing would only report a\n        # broken reviewer for an ordinary opt-in race, and the labeled event that\n        # follows carries the real verdict. A manual dispatch has no such follow-up\n        # and its request would vanish, so that one still fails.\n        if manual_request:\n            raise PolicyError("review_mode_label_mismatch")\n        return PolicyDecision(\n            False, request.review_mode, "review_mode_label_mismatch", ""\n        )\n',
    '    if request.review_mode != label_mode and not (\n        manual_request and label_mode == "auto"\n    ):\n        raise PolicyError("review_mode_label_mismatch")\n',
)


def restore_pre_v166_label_mismatch_decline(repo: Path) -> None:
    """Restore the pre-v1.66 skipped notices and the failing label-mismatch guard.

    v1.66 declines an event-triggered label change instead of failing it, and brings
    the OpenCode notice to parity so no reviewer reports a false cause. This runs
    first (newest release first) so the older restores still find their text.
    """

    # Idempotent: a tree that already restored (or never had) the v1.66 text is left
    # alone, so this can also run first inside the older restore chains.
    for workflow, (current, historical) in PRE_V166_SKIPPED_JOBS.items():
        path = repo / ".github/workflows" / workflow
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert text.count(current) <= 1, workflow
        path.write_text(text.replace(current, historical), encoding="utf-8")
    helper = repo / ".github/actions/resolve-review-policy/resolve_review_policy.py"
    if helper.exists():
        text = helper.read_text(encoding="utf-8")
        current, historical = PRE_V166_MISMATCH_HUNK
        assert text.count(current) <= 1
        helper.write_text(text.replace(current, historical), encoding="utf-8")


PRE_V171_OPENCODE_DISMISSAL_HUNKS = {
    '.github/workflows/opencode-auto-review.yml': (
        (
            "              GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0',\n              GIT_ASKPASS: '/bin/false', SSH_ASKPASS: '/bin/false', GIT_EXTERNAL_DIFF: '',\n            };\n            let before, sealedEvidence, handoff;\n            let dismissedFindingIds = new Set();\n            try {\n              const { data: artifact } = await github.rest.actions.getArtifact({\n                owner: context.repo.owner, repo: context.repo.repo,\n                artifact_id: Number(process.env.HANDOFF_ARTIFACT_ID),\n",
            "              GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0',\n              GIT_ASKPASS: '/bin/false', SSH_ASKPASS: '/bin/false', GIT_EXTERNAL_DIFF: '',\n            };\n            let before, sealedEvidence, handoff;\n            try {\n              const { data: artifact } = await github.rest.actions.getArtifact({\n                owner: context.repo.owner, repo: context.repo.repo,\n                artifact_id: Number(process.env.HANDOFF_ARTIFACT_ID),\n",
        ),
        (
            '                || claimCheckpoint.handoff.decision !== budgetDecision\n                || claimCheckpoint.handoff.stop_reason !== budgetDecision) {\n                throw new Error(\'sealed budget claim checkpoint identity mismatch\');\n              }\n              // The dismissal list rides in the sealed budget checkpoint this job already\n              // hash-binds (handoff.files[\'review-budget-claim.json\']), so it needs no extra\n              // handoff field and no second copy. Its shape is validated here because the\n              // ledger is the budget action\'s output rather than this workflow\'s: an\n              // unexpected shape must fail closed instead of silently dropping a dismissal.\n              const ledgerDismissals = claimCheckpoint.ledger.dismissed_findings ?? [];\n              if (!Array.isArray(ledgerDismissals) || ledgerDismissals.length > 16\n                || ledgerDismissals.some((item) => !item || typeof item !== \'object\'\n                  || Array.isArray(item)\n                  || JSON.stringify(Object.keys(item).sort()) !== \'["comment_id","finding_id"]\'\n                  || !Number.isSafeInteger(item.comment_id) || item.comment_id <= 0\n                  || typeof item.finding_id !== \'string\'\n                  || !/^RVW-[0-9a-f]{12}$/.test(item.finding_id))) {\n                throw new Error(\'dismissed findings invalid\');\n              }\n              dismissedFindingIds = new Set(ledgerDismissals.map((item) => item.finding_id));\n              const exactClaims = Array.isArray(claimCheckpoint.ledger.invocations)\n                ? claimCheckpoint.ledger.invocations.filter((item) => item.run_id === runId\n                  && item.run_attempt === handoff.run_attempt\n                  && item.head_sha === attemptHead\n',
            "                || claimCheckpoint.handoff.decision !== budgetDecision\n                || claimCheckpoint.handoff.stop_reason !== budgetDecision) {\n                throw new Error('sealed budget claim checkpoint identity mismatch');\n              }\n              const exactClaims = Array.isArray(claimCheckpoint.ledger.invocations)\n                ? claimCheckpoint.ledger.invocations.filter((item) => item.run_id === runId\n                  && item.run_attempt === handoff.run_attempt\n                  && item.head_sha === attemptHead\n",
        ),
        (
            "                return false;\n              }\n              return true;\n            };\n            const reserved = /^(## OpenCode Review \\(latest\\)|<!-- automation:|<!-- automation-candidate:|<!-- automation-state:|- Status: (success|failure|stale)$|- Run: https?:\\/\\/\\S+$|- Attestation: [1-9][0-9]*$|- Reviewed: [0-9a-f]{40}$|- Validation: filtered_invalid_new_findings=[1-9][0-9]*; reasons=[a-z_,]+$|- Carryover: normalized_carryover_blocks=[1-9][0-9]*; reasons=[a-z_,]+$|- Filtered candidate \\(raw\\): artifact `opencode-candidate-[1-9][0-9]*-[1-9][0-9]*` → `review\\.md`$|- Last attempt: failure \\(https?:\\/\\/\\S+\\)$|- Reason: [a-z_]+$)/;\n            const clean = (body) => {\n              const rawLines = (body || '').split('\\n');\n              // OpenCode can include tool/thinking traces in the final text event. Only an exact\n              // final-review marker starts a candidate suffix; the strict grammar and changed-line\n",
            "                return false;\n              }\n              return true;\n            };\n            const reserved = /^(## OpenCode Review \\(latest\\)|<!-- automation:|<!-- automation-candidate:|<!-- automation-state:|- Status: (success|failure|stale)$|- Run: https?:\\/\\/\\S+$|- Attestation: [1-9][0-9]*$|- Reviewed: [0-9a-f]{40}$|- Validation: filtered_invalid_new_findings=[1-9][0-9]*; reasons=[a-z_,]+$|- Filtered candidate \\(raw\\): artifact `opencode-candidate-[1-9][0-9]*-[1-9][0-9]*` → `review\\.md`$|- Last attempt: failure \\(https?:\\/\\/\\S+\\)$|- Reason: [a-z_]+$)/;\n            const clean = (body) => {\n              const rawLines = (body || '').split('\\n');\n              // OpenCode can include tool/thinking traces in the final text event. Only an exact\n              // final-review marker starts a candidate suffix; the strict grammar and changed-line\n",
        ),
        (
            '              return evidence;\n            };\n            const priorActiveHeadings = new Map();\n            const priorActiveEvidence = new Map();\n            const priorActiveBlocks = new Map();\n            if (priorSuccess) {\n              const priorSections = splitSections(previousBody);\n              if (priorSections) {\n                for (const section of priorSections.filter((item) =>\n',
            '              return evidence;\n            };\n            const priorActiveHeadings = new Map();\n            const priorActiveEvidence = new Map();\n            if (priorSuccess) {\n              const priorSections = splitSections(previousBody);\n              if (priorSections) {\n                for (const section of priorSections.filter((item) =>\n',
        ),
        (
            '                        (priorActiveHeadings.get(block.heading) || 0) + 1);\n                      const evidence = priorActiveEvidence.get(block.heading) || [];\n                      evidence.push(parseBlockEvidence(block));\n                      priorActiveEvidence.set(block.heading, evidence);\n                      priorActiveBlocks.set(block.heading, block);\n                    }\n                  }\n                }\n              }\n',
            '                        (priorActiveHeadings.get(block.heading) || 0) + 1);\n                      const evidence = priorActiveEvidence.get(block.heading) || [];\n                      evidence.push(parseBlockEvidence(block));\n                      priorActiveEvidence.set(block.heading, evidence);\n                    }\n                  }\n                }\n              }\n',
        ),
        (
            "              const carryover = sections.filter((item) => item.name !== 'New findings');\n              if (carryover.length && !priorSuccess) return null;\n              const parsedBlocks = [];\n              const filteredNewFindings = [];\n              const normalizedBlocks = [];\n              const currentHeadings = new Set();\n              for (const [sectionIndex, section] of sections.entries()) {\n                const sectionFindingBlocks = sectionBlocks(section);\n                if (sectionFindingBlocks === null) return null;\n                for (const block of sectionFindingBlocks) {\n                  if (currentHeadings.has(block.heading)) return null;\n                  currentHeadings.add(block.heading);\n                  // A dismissed finding left the active set, so the model repeating it binds\n                  // to nothing. Normalizing it out here keeps that from failing the whole\n                  // document, and omitting it from the published body is what actually\n                  // retires it: the body is the active set the next round reads back.\n                  const carriedId = CARRIED_FINDING_HEADING.exec(block.heading);\n                  if (carriedId && dismissedFindingIds.has(carriedId[1])) {\n                    normalizedBlocks.push({ block, reason: 'dismissed_prior_id' });\n                    continue;\n                  }\n                  const priorMatches = priorActiveHeadings.get(block.heading) || 0;\n                  if (section.name === 'New findings' && priorMatches !== 0) return null;\n                  if (section.name !== 'New findings' && priorMatches !== 1) return null;\n                  const evidence = parseBlockEvidence(block);\n",
            "              const carryover = sections.filter((item) => item.name !== 'New findings');\n              if (carryover.length && !priorSuccess) return null;\n              const parsedBlocks = [];\n              const filteredNewFindings = [];\n              const currentHeadings = new Set();\n              for (const [sectionIndex, section] of sections.entries()) {\n                const sectionFindingBlocks = sectionBlocks(section);\n                if (sectionFindingBlocks === null) return null;\n                for (const block of sectionFindingBlocks) {\n                  if (currentHeadings.has(block.heading)) return null;\n                  currentHeadings.add(block.heading);\n                  const priorMatches = priorActiveHeadings.get(block.heading) || 0;\n                  if (section.name === 'New findings' && priorMatches !== 0) return null;\n                  if (section.name !== 'New findings' && priorMatches !== 1) return null;\n                  const evidence = parseBlockEvidence(block);\n",
        ),
        (
            '                  parsedBlocks.push({ sectionIndex, section: section.name, block,\n                    evidence: { anchors: [], removals: [removal] } });\n                }\n              }\n              return { blocks: parsedBlocks, sections, filteredNewFindings, normalizedBlocks };\n            };\n\n            const changedPathValidation = new Map();\n            const validateAnchorEvidence = (evidence) => {\n',
            '                  parsedBlocks.push({ sectionIndex, section: section.name, block,\n                    evidence: { anchors: [], removals: [removal] } });\n                }\n              }\n              return { blocks: parsedBlocks, sections, filteredNewFindings };\n            };\n\n            const changedPathValidation = new Map();\n            const validateAnchorEvidence = (evidence) => {\n',
        ),
        (
            "            const review = candidate ? clean(candidate.body) : '';\n            const parsedReview = review ? parseReview(review) : null;\n            const filteredNewFindings = parsedReview\n              ? [...parsedReview.filteredNewFindings] : [];\n            const normalizedCarryover = parsedReview\n              ? [...parsedReview.normalizedBlocks] : [];\n            let anchorValidationFailed = false;\n            if (parsedReview) {\n              const newFindings = parsedReview.blocks.filter((entry) =>\n                entry.section === 'New findings');\n",
            "            const review = candidate ? clean(candidate.body) : '';\n            const parsedReview = review ? parseReview(review) : null;\n            const filteredNewFindings = parsedReview\n              ? [...parsedReview.filteredNewFindings] : [];\n            let anchorValidationFailed = false;\n            if (parsedReview) {\n              const newFindings = parsedReview.blocks.filter((entry) =>\n                entry.section === 'New findings');\n",
        ),
        (
            "                }\n              }\n              const carryover = parsedReview.blocks.filter((entry) =>\n                entry.section !== 'New findings');\n              // Carryover anchors are checked one block at a time. Aggregating them meant a\n              // single stale anchor — normal once the head advances past a previous finding —\n              // discarded the whole review including this round's new findings.\n              for (const entry of carryover) {\n                if (anchorValidationFailed) break;\n                const result = validateAnchorEvidence(entry.evidence);\n                if (result === 'valid') continue;\n                if (result !== 'invalid') { anchorValidationFailed = true; break; }\n                // The model's evidence is out of scope, but nothing proved the finding fixed.\n                // Dropping the block would retire it: the published body is the active set the\n                // next round reads back, so an omitted finding leaves the budget ledger's\n                // remaining IDs. Republish the prior round's canonical block instead — it was\n                // already validated and attested. A prior body this job could not parse leaves\n                // the map empty, which fails closed exactly as it did before.\n                const priorBlock = priorActiveBlocks.get(entry.block.heading);\n                if (!priorBlock) { anchorValidationFailed = true; break; }\n                entry.block = priorBlock;\n                // A disposition the model cannot prove is discarded rather than believed. The\n                // finding returns to `Still open`: leaving it under `Resolved`/`Retracted` would\n                // publish an unproven fix, and omitting it would retire a finding nothing fixed,\n                // because the published body is the active set the next round reads back.\n                if (entry.section !== 'Still open') {\n                  let stillOpenIndex = parsedReview.sections\n                    .findIndex((item) => item.name === 'Still open');\n                  if (stillOpenIndex === -1) {\n                    stillOpenIndex = parsedReview.sections.length;\n                    parsedReview.sections.push({ name: 'Still open', lines: [] });\n                  }\n                  entry.section = 'Still open';\n                  entry.sectionIndex = stillOpenIndex;\n                }\n                normalizedCarryover.push({ entry, reason: 'invalid_anchor' });\n              }\n            }\n            const filteredEntries = new Set(filteredNewFindings.map((item) => item.entry));\n            const canonicalReview = parsedReview\n              ? parsedReview.sections.map((section, sectionIndex) => {\n                  const retained = parsedReview.blocks.filter((entry) =>\n                    entry.sectionIndex === sectionIndex && !filteredEntries.has(entry));\n                  const sectionBody = retained.length === 0\n                    ? 'None'\n                    : retained.map((entry) => [renderHeading(entry), ...entry.block.lines]\n                      .join('\\n').trim()).join('\\n\\n');\n                  return `### ${section.name}\\n${sectionBody}`;\n",
            "                }\n              }\n              const carryover = parsedReview.blocks.filter((entry) =>\n                entry.section !== 'New findings');\n              if (!anchorValidationFailed && carryover.length > 0) {\n                const carryoverEvidence = {\n                  anchors: carryover.flatMap((entry) => entry.evidence.anchors),\n                  removals: carryover.flatMap((entry) => entry.evidence.removals),\n                };\n                if (validateAnchorEvidence(carryoverEvidence) !== 'valid') {\n                  anchorValidationFailed = true;\n                }\n              }\n            }\n            const filteredEntries = new Set(filteredNewFindings.map((item) => item.entry));\n            const canonicalReview = parsedReview\n              ? parsedReview.sections.map((section, sectionIndex) => {\n                  const retained = parsedReview.blocks.filter((entry) =>\n                    entry.sectionIndex === sectionIndex && !filteredEntries.has(entry));\n                  const sectionBody = section.name === 'New findings' && retained.length === 0\n                    ? 'None'\n                    : retained.map((entry) => [renderHeading(entry), ...entry.block.lines]\n                      .join('\\n').trim()).join('\\n\\n');\n                  return `### ${section.name}\\n${sectionBody}`;\n",
        ),
        (
            "              .sort().join(',');\n            const validationSummary = modelSucceeded && filteredNewFindings.length > 0\n              ? `\\n- Validation: filtered_invalid_new_findings=${filteredNewFindings.length}; reasons=${filteredReasons}`\n              : '';\n            // Carryover normalization is counted apart from the New findings filter. Sharing one\n            // counter would report a round that only rewrote a carryover as `quality_filtered`\n            // and make the `filtered_invalid_new_findings=` label untrue.\n            const normalizedReasons = [...new Set(normalizedCarryover.map((item) => item.reason))]\n              .sort().join(',');\n            const carryoverSummary = modelSucceeded && normalizedCarryover.length > 0\n              ? `\\n- Carryover: normalized_carryover_blocks=${normalizedCarryover.length};`\n                + ` reasons=${normalizedReasons}`\n              : '';\n            const filteredCandidateSummary = modelSucceeded && filteredNewFindings.length > 0\n              ? `\\n- Filtered candidate (raw): artifact \\`opencode-candidate-${runId}-${runAttempt}\\` → \\`review.md\\``\n              : '';\n            const retainedNewFindingCount = parsedReview\n              ? parsedReview.blocks.filter((entry) => entry.section === 'New findings'\n                && !filteredEntries.has(entry)).length : 0;\n            const qualityFiltered = modelSucceeded && filteredNewFindings.length > 0\n              && retainedNewFindingCount === 0;\n            const bodyFor = (attestationId) => `${header}\\n${marker}\\n<!-- automation-state:${JSON.stringify(state)} -->\\n${legacyMarker}\\n\\n- Status: ${status}\\n- Run: ${process.env.RUN_URL}\\n- Attestation: ${attestationId}${succeeded ? `\\n- Reviewed: ${attemptHead}` : ''}${validationSummary}${carryoverSummary}${filteredCandidateSummary}${!succeeded ? `\\n- Last attempt: failure (${process.env.RUN_URL})\\n- Reason: ${failureReason}` : ''}\\n\\n${displayBody}`;\n            if (Buffer.byteLength(bodyFor(Number.MAX_SAFE_INTEGER), 'utf8') > 65536) {\n              throw new Error('canonical OpenCode comment exceeds 65,536-byte publication limit');\n            }\n            if (!(await repairComments())) return;\n",
            "              .sort().join(',');\n            const validationSummary = modelSucceeded && filteredNewFindings.length > 0\n              ? `\\n- Validation: filtered_invalid_new_findings=${filteredNewFindings.length}; reasons=${filteredReasons}`\n              : '';\n            const filteredCandidateSummary = modelSucceeded && filteredNewFindings.length > 0\n              ? `\\n- Filtered candidate (raw): artifact \\`opencode-candidate-${runId}-${runAttempt}\\` → \\`review.md\\``\n              : '';\n            const retainedNewFindingCount = parsedReview\n              ? parsedReview.blocks.filter((entry) => entry.section === 'New findings'\n                && !filteredEntries.has(entry)).length : 0;\n            const qualityFiltered = modelSucceeded && filteredNewFindings.length > 0\n              && retainedNewFindingCount === 0;\n            const bodyFor = (attestationId) => `${header}\\n${marker}\\n<!-- automation-state:${JSON.stringify(state)} -->\\n${legacyMarker}\\n\\n- Status: ${status}\\n- Run: ${process.env.RUN_URL}\\n- Attestation: ${attestationId}${succeeded ? `\\n- Reviewed: ${attemptHead}` : ''}${validationSummary}${filteredCandidateSummary}${!succeeded ? `\\n- Last attempt: failure (${process.env.RUN_URL})\\n- Reason: ${failureReason}` : ''}\\n\\n${displayBody}`;\n            if (Buffer.byteLength(bodyFor(Number.MAX_SAFE_INTEGER), 'utf8') > 65536) {\n              throw new Error('canonical OpenCode comment exceeds 65,536-byte publication limit');\n            }\n            if (!(await repairComments())) return;\n",
        ),
    ),
    '.github/actions/review-invocation-budget/review_invocation_budget.py': (
        (
            'def choose_dismissals(\n        state: LedgerState, events: Sequence[DismissEvent]) -> tuple[DismissedFinding, ...]:\n    """Bind each dismissed finding to the earliest authorizing comment, bounded and sorted."""\n    _validate_dismiss_events(tuple(events))\n    earliest: dict[str, int] = {}\n    for event in events:\n        if event.actor_permission not in _DISMISS_PERMISSIONS:\n            continue\n',
            'def choose_dismissals(\n        state: LedgerState, events: Sequence[DismissEvent]) -> tuple[DismissedFinding, ...]:\n    """Bind each dismissed finding to the earliest authorizing comment, bounded and sorted."""\n    _validate_dismiss_events(tuple(events))\n    # OpenCode assigns no RVW- IDs, so a dismissal can never name one of its findings;\n    # recording one would advertise a path that nothing downstream consumes.\n    if state.reviewer == "opencode":\n        return ()\n    earliest: dict[str, int] = {}\n    for event in events:\n        if event.actor_permission not in _DISMISS_PERMISSIONS:\n            continue\n',
        ),
        (
            '    if not isinstance(server_url, str) or not server_url or "\\n" in server_url or "\\r" in server_url:\n        raise BudgetStateError("server_url_invalid")\n    base_url = server_url.rstrip(\'/\')\n    run_url = f"{base_url}/{state.repository}/actions/runs/{handoff.current_run_id}"\n    dismissed = ", ".join(\n        f"[{item.finding_id}]({base_url}/{state.repository}/pull/{state.pr}#issuecomment-{item.comment_id})"\n        for item in state.dismissed_findings\n    ) or "none"\n    dismissed_line = f"- Dismissed findings: {dismissed}\\n"\n    dismissal_guidance = (\n        " A collaborator with write permission can dismiss a false positive by commenting "\n        "`dismiss RVW-<12 hex> <reason>` on this pull request; the dismissal takes effect on "\n        "the next review run and is revoked by deleting that comment."\n    )\n    return (\n        f"## {_REVIEWER_TITLES[state.reviewer]} review invocation budget\\n"\n        f"- Decision: {handoff.decision}\\n"\n        f"- Automatic rounds: {handoff.automatic_rounds}/{state.budgets.max_rounds}\\n"\n',
            '    if not isinstance(server_url, str) or not server_url or "\\n" in server_url or "\\r" in server_url:\n        raise BudgetStateError("server_url_invalid")\n    base_url = server_url.rstrip(\'/\')\n    run_url = f"{base_url}/{state.repository}/actions/runs/{handoff.current_run_id}"\n    dismissed_line = ""\n    dismissal_guidance = ""\n    if state.reviewer != "opencode":\n        dismissed = ", ".join(\n            f"[{item.finding_id}]({base_url}/{state.repository}/pull/{state.pr}#issuecomment-{item.comment_id})"\n            for item in state.dismissed_findings\n        ) or "none"\n        dismissed_line = f"- Dismissed findings: {dismissed}\\n"\n        dismissal_guidance = (\n            " A collaborator with write permission can dismiss a false positive by commenting "\n            "`dismiss RVW-<12 hex> <reason>` on this pull request; the dismissal takes effect on "\n            "the next review run and is revoked by deleting that comment."\n        )\n    return (\n        f"## {_REVIEWER_TITLES[state.reviewer]} review invocation budget\\n"\n        f"- Decision: {handoff.decision}\\n"\n        f"- Automatic rounds: {handoff.automatic_rounds}/{state.budgets.max_rounds}\\n"\n',
        ),
    ),
}


def restore_pre_v171_opencode_dismissals(repo: Path) -> None:
    """Restore the pre-v1.71 OpenCode canonicalizer and budget helper.

    v1.71 applies dismissals to OpenCode, validates carryover anchors one block at a
    time, and stops hiding the dismissal line from that reviewer's ledger comment.
    This runs first (newest release first) so the older restores still find their text.
    """

    for relative, hunks in PRE_V171_OPENCODE_DISMISSAL_HUNKS.items():
        path = repo / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Idempotent, and applied bottom-up so neighbouring hunks keep the context they
        # match on: a tree that already restored (or never had) the v1.71 text is left alone.
        for current, historical in reversed(hunks):
            assert text.count(current) <= 1, relative
            text = text.replace(current, historical)
        path.write_text(text, encoding="utf-8")


PRE_V170_OPENCODE_FINDING_ID_HUNKS = (
    (
        '            - Current line: "exact complete added-side source line"\n            Continue with concise causal and impact prose. If a defect spans multiple source lines,\n            choose the single added-side line that most directly causes it and describe supporting\n            lines in prose without adding another Changed anchor or Current line field.\n\n            The severity in a New finding heading is exactly one of `CRITICAL`, `HIGH`, or `MEDIUM`,\n            spelled that way inside square brackets. The workflow assigns each published finding\n            a stable `RVW-` identifier and prints it in the heading; never write one yourself.\n            A re-review carryover heading already carries that identifier — copy it unchanged.\n\n            TRUSTED REVIEW SCOPE (final authority): `review-full.diff` and `review-scope.json`\n            are the exclusive set of changes under review. Always read `review-full.diff`, including when the prepared\n            mode is delta. Do not use an unnumbered or model-side diff fallback, another diff file,\n',
        '            - Current line: "exact complete added-side source line"\n            Continue with concise causal and impact prose. If a defect spans multiple source lines,\n            choose the single added-side line that most directly causes it and describe supporting\n            lines in prose without adding another Changed anchor or Current line field.\n\n            TRUSTED REVIEW SCOPE (final authority): `review-full.diff` and `review-scope.json`\n            are the exclusive set of changes under review. Always read `review-full.diff`, including when the prepared\n            mode is delta. Do not use an unnumbered or model-side diff fallback, another diff file,\n',
    ),
    (
        "              }\n              return 'valid';\n            };\n\n            // A finding carries a workflow-owned RVW- id so a collaborator's dismissal comment\n            // can name it. The construction is the shared canonicalizer's — NUL-separated\n            // reviewer, path, line, severity and normalized title, sha256, first 12 hex — with\n            // reviewer 'opencode'. Only the title normalization differs: this one collapses\n            // whitespace and lowercases, and applies neither the shared implementation's\n            // visible-text escaping nor its Unicode case folding, because OpenCode publishes the\n            // title verbatim and JavaScript has no case folding. Ids are namespaced by reviewer,\n            // so nothing compares the two. Severity is read from the heading rather than required\n            // by the grammar: a heading whose severity is absent or unrecognized keeps its exact\n            // bytes and receives no id, which leaves every finding published today byte-identical.\n            const FINDING_HEADING = /^#### \\[(CRITICAL|HIGH|MEDIUM)\\] (\\S.*)$/;\n            const CARRIED_FINDING_HEADING = /^#### (RVW-[0-9a-f]{12}) \\[(CRITICAL|HIGH|MEDIUM)\\] (\\S.*)$/;\n            const normalizeTitle = (title) => title.trim().split(/\\s+/).join(' ').toLowerCase();\n            const deriveFindingId = (path, line, severity, title) => 'RVW-' + sha256(\n              ['opencode', path, String(line), severity, normalizeTitle(title)].join('\\0')\n            ).slice(0, 12);\n            const renderHeading = (entry) => {\n              const parts = FINDING_HEADING.exec(entry.block.heading);\n              if (!parts) return entry.block.heading;\n              const anchor = entry.evidence.anchors[0];\n              if (!anchor) return entry.block.heading;\n              return `#### ${deriveFindingId(anchor.path, anchor.line, parts[1], parts[2])}`\n                + ` [${parts[1]}] ${parts[2]}`;\n            };\n            // An id is assigned once, when the finding is new, and every later round inherits\n            // it through the heading the model copies verbatim. Re-deriving would move the id\n            // whenever the anchor moves and silently void a dismissal that names it.\n            const blockFindingId = (heading, anchor) => {\n              const carried = CARRIED_FINDING_HEADING.exec(heading);\n              if (carried) return carried[1];\n              const parts = FINDING_HEADING.exec(heading);\n              if (!parts || !anchor) return null;\n              return deriveFindingId(anchor.path, anchor.line, parts[1], parts[2]);\n            };\n            // Before ids existed the heading string alone stopped the model from re-reporting a\n            // finding it was also carrying over (`priorMatches !== 0` below). Once a prior\n            // heading gains an id and the model's new heading has none, the two strings differ,\n            // so the derived id has to enforce that same rule.\n            const priorActiveIds = new Set();\n            for (const [heading, evidenceList] of priorActiveEvidence) {\n              const priorId = blockFindingId(\n                heading, evidenceList[0] && evidenceList[0].changedAnchors[0]);\n              if (priorId) priorActiveIds.add(priorId);\n            }\n            const candidate = candidateArtifactValid ? { body: candidateReview } : null;\n            const review = candidate ? clean(candidate.body) : '';\n            const parsedReview = review ? parseReview(review) : null;\n            const filteredNewFindings = parsedReview\n",
        "              }\n              return 'valid';\n            };\n\n            const candidate = candidateArtifactValid ? { body: candidateReview } : null;\n            const review = candidate ? clean(candidate.body) : '';\n            const parsedReview = review ? parseReview(review) : null;\n            const filteredNewFindings = parsedReview\n",
    ),
    (
        "            let anchorValidationFailed = false;\n            if (parsedReview) {\n              const newFindings = parsedReview.blocks.filter((entry) =>\n                entry.section === 'New findings');\n              const duplicatePriorEntries = new Set();\n              for (const entry of newFindings) {\n                // Ids belong to the workflow. A new heading that already carries one was\n                // written by the model, and publishing it would let the model choose what the\n                // budget ledger records as a remaining finding.\n                if (CARRIED_FINDING_HEADING.test(entry.block.heading)) {\n                  duplicatePriorEntries.add(entry);\n                  filteredNewFindings.push({ entry, reason: 'model_assigned_id' });\n                  continue;\n                }\n                const findingId = blockFindingId(\n                  entry.block.heading, entry.evidence.anchors[0]);\n                if (findingId === null || !priorActiveIds.has(findingId)) continue;\n                duplicatePriorEntries.add(entry);\n                filteredNewFindings.push({ entry, reason: 'duplicate_prior_id' });\n              }\n              for (const entry of newFindings) {\n                if (duplicatePriorEntries.has(entry)) continue;\n                const result = validateAnchorEvidence(entry.evidence);\n                if (result === 'valid') continue;\n                if (result === 'invalid') {\n                  filteredNewFindings.push({ entry, reason: 'anchor_out_of_scope' });\n",
        "            let anchorValidationFailed = false;\n            if (parsedReview) {\n              const newFindings = parsedReview.blocks.filter((entry) =>\n                entry.section === 'New findings');\n              for (const entry of newFindings) {\n                const result = validateAnchorEvidence(entry.evidence);\n                if (result === 'valid') continue;\n                if (result === 'invalid') {\n                  filteredNewFindings.push({ entry, reason: 'anchor_out_of_scope' });\n",
    ),
    (
        "                }\n              }\n            }\n            const filteredEntries = new Set(filteredNewFindings.map((item) => item.entry));\n            const canonicalReview = parsedReview\n              ? parsedReview.sections.map((section, sectionIndex) => {\n                  const retained = parsedReview.blocks.filter((entry) =>\n                    entry.sectionIndex === sectionIndex && !filteredEntries.has(entry));\n                  const sectionBody = section.name === 'New findings' && retained.length === 0\n                    ? 'None'\n                    : retained.map((entry) => [renderHeading(entry), ...entry.block.lines]\n                      .join('\\n').trim()).join('\\n\\n');\n                  return `### ${section.name}\\n${sectionBody}`;\n                }).join('\\n\\n')\n              : review;\n",
        "                }\n              }\n            }\n            const filteredEntries = new Set(filteredNewFindings.map((item) => item.entry));\n            const canonicalReview = parsedReview && filteredEntries.size > 0\n              ? parsedReview.sections.map((section, sectionIndex) => {\n                  const retained = parsedReview.blocks.filter((entry) =>\n                    entry.sectionIndex === sectionIndex && !filteredEntries.has(entry));\n                  const sectionBody = section.name === 'New findings' && retained.length === 0\n                    ? 'None'\n                    : retained.map((entry) => [entry.block.heading, ...entry.block.lines]\n                      .join('\\n').trim()).join('\\n\\n');\n                  return `### ${section.name}\\n${sectionBody}`;\n                }).join('\\n\\n')\n              : review;\n",
    ),
)


def restore_pre_v170_opencode_finding_ids(repo: Path) -> None:
    """Restore the OpenCode workflow to its pre-v1.70 bytes, before finding identifiers.

    v1.70 stamps a stable `RVW-` identifier on every published OpenCode finding and
    re-renders the canonical body on every successful round so the heading can carry
    it. This runs first (newest release first) so the older restores still find their
    text.
    """

    path = repo / ".github/workflows/opencode-auto-review.yml"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    # Idempotent: a tree that already restored (or never had) the v1.70 text is left
    # alone. Applied bottom-up so neighbouring hunks keep the context they match on.
    for current, historical in reversed(PRE_V170_OPENCODE_FINDING_ID_HUNKS):
        assert text.count(current) <= 1
        text = text.replace(current, historical)
    path.write_text(text, encoding="utf-8")


RETIRED_MANUAL_PR_REVIEW_ENTRIES = {'.github/workflows/gemini-pr-review.yml': {'path': '.github/workflows/gemini-pr-review.yml', 'kind': 'required', 'central_workflow': 'gemini-review.yml', 'auth_family': 'gemini', 'profile_axis': 'repo_write_auth', 'trigger': {'workflow_dispatch': {'inputs': {'pr_number': {'description': 'Pull request number to review (e.g. 45)', 'required': 'true', 'type': 'string'}, 'additional_context': {'description': 'Optional extra context for the review prompt', 'required': 'false', 'type': 'string'}}}}, 'caller_jobs': [{'name': 'review', 'permissions': {'contents': 'read', 'issues': 'write', 'pull-requests': 'write'}, 'with': ['additional_context', 'app_id', 'issue_body', 'issue_title', 'pr_number', 'repo_write_auth'], 'secrets': ['APP_PRIVATE_KEY', 'GEMINI_API_KEY']}]}, '.github/workflows/gemini-review.yml': {'path': '.github/workflows/gemini-review.yml', 'kind': 'required', 'central_workflow': 'gemini-review.yml', 'auth_family': 'gemini', 'profile_axis': 'repo_write_auth', 'trigger': {'workflow_call': {'inputs': {'pr_number': {'type': 'string', 'description': 'Pull request number', 'required': 'true'}, 'issue_title': {'type': 'string', 'description': 'Pull request title', 'required': 'true'}, 'issue_body': {'type': 'string', 'description': 'Pull request body', 'required': 'true'}, 'additional_context': {'type': 'string', 'description': 'Any additional context from the request', 'required': 'false'}}}}, 'caller_jobs': [{'name': 'review', 'permissions': {'contents': 'read', 'issues': 'write', 'pull-requests': 'write'}, 'with': ['additional_context', 'app_id', 'issue_body', 'issue_title', 'pr_number', 'repo_write_auth'], 'secrets': ['APP_PRIVATE_KEY', 'GEMINI_API_KEY']}]}}

RETIRED_MANUAL_PR_REVIEW_CALLERS = {
    'gemini-pr-review.yml': (
        'name: Gemini Manual PR Review\n\non:\n  workflow_dispatch:\n    inputs:\n      pr_number:\n        description: \'Pull request number to review (e.g. 45)\'\n        required: true\n        type: string\n      additional_context:\n        description: \'Optional extra context for the review prompt\'\n        required: false\n        type: string\n\njobs:\n  prepare:\n    runs-on: ubuntu-latest\n    permissions:\n      pull-requests: read\n    outputs:\n      pr_title: ${{ steps.pr.outputs.title }}\n      pr_body: ${{ steps.pr.outputs.body }}\n    steps:\n      - name: Validate pr_number\n        shell: bash\n        env:\n          PR_NUMBER: ${{ inputs.pr_number }}\n        run: |\n          if ! [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]; then\n            echo "pr_number must be a positive integer"\n            exit 1\n          fi\n\n      - name: Fetch PR\n        id: pr\n        shell: bash\n        env:\n          GH_TOKEN: ${{ github.token }}\n          REPO: ${{ github.repository }}\n          PR_NUMBER: ${{ inputs.pr_number }}\n        run: |\n          title="$(gh pr view "$PR_NUMBER" --repo "$REPO" --json title --jq .title)"\n          body="$(gh pr view "$PR_NUMBER" --repo "$REPO" --json body --jq .body)"\n\n          write_output() {\n            local name="$1"\n            local value="$2"\n            local delimiter=\'__AUTOMATION_OUTPUT__\'\n            while [[ "$value" == *"$delimiter"* ]]; do\n              delimiter="${delimiter}_X"\n            done\n            {\n              printf \'%s<<%s\\n\' "$name" "$delimiter"\n              printf \'%s\\n\' "$value"\n              printf \'%s\\n\' "$delimiter"\n            } >> "$GITHUB_OUTPUT"\n          }\n\n          write_output title "$title"\n          write_output body "$body"\n\n  review:\n    needs: prepare\n    permissions:\n      contents: read\n      issues: write\n      pull-requests: write\n    uses: jhw7500/automation/.github/workflows/gemini-review.yml@__AUTOMATION_COMMIT__\n    with:\n      pr_number: ${{ inputs.pr_number }}\n      issue_title: ${{ needs.prepare.outputs.pr_title }}\n      issue_body: ${{ needs.prepare.outputs.pr_body }}\n      additional_context: ${{ inputs.additional_context }}\n      repo_write_auth: github_app\n      app_id: ${{ vars.APP_ID }}\n    secrets:\n      APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}\n      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}\n'
    ),
    'gemini-review.yml': (
        'name: Gemini Review\n\non:\n  workflow_call:\n    inputs:\n      pr_number:\n        type: string\n        description: Pull request number\n        required: true\n      issue_title:\n        type: string\n        description: Pull request title\n        required: true\n      issue_body:\n        type: string\n        description: Pull request body\n        required: true\n      additional_context:\n        type: string\n        description: Any additional context from the request\n        required: false\n\njobs:\n  review:\n    permissions:\n      contents: read\n      issues: write\n      pull-requests: write\n    uses: jhw7500/automation/.github/workflows/gemini-review.yml@__AUTOMATION_COMMIT__\n    with:\n      pr_number: ${{ inputs.pr_number }}\n      issue_title: ${{ inputs.issue_title }}\n      issue_body: ${{ inputs.issue_body }}\n      additional_context: ${{ inputs.additional_context }}\n      repo_write_auth: github_app\n      app_id: ${{ vars.APP_ID }}\n    secrets:\n      APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}\n      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}\n'
    ),
}


def restore_retired_manual_pr_review(repo: Path) -> None:
    """Put back the callers v1.68 withdrew, for fixtures on older release lines.

    v1.68 retires the workflow_dispatch pull-request review, but a release before
    it still shipped both callers and the manual-output contract still describes
    them. This runs first (newest release first) like the other restores.
    """

    root = repo / "examples/baseline-workflows/.github/workflows"
    if not root.exists():
        return
    for name, body in RETIRED_MANUAL_PR_REVIEW_CALLERS.items():
        path = root / name
        # Idempotent: a fixture that still carries the caller is left alone.
        if not path.exists():
            path.write_text(body, encoding="utf-8")
    catalog = repo / "scripts/workflow-catalog.json"
    if not catalog.exists():
        return
    # The canonical tree is checked against the catalog in the same tree, so the
    # entries have to come back with the files.
    data = json.loads(catalog.read_text(encoding="utf-8"))
    changed = False
    for entry in data.get("entries", []):
        historical = RETIRED_MANUAL_PR_REVIEW_ENTRIES.get(entry.get("path"))
        if historical is not None and entry.get("kind") == "retired":
            entry.clear()
            entry.update(historical)
            changed = True
    if changed:
        catalog.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    config = repo / "examples/baseline-workflows/.github/workflow-config.yml"
    if not config.exists():
        return
    # The bootstrap contract requires an entry for every managed workflow, so the
    # disabled keys come back with the callers.
    text = config.read_text(encoding="utf-8")
    anchor = "  gemini-scheduled-triage:\n"
    keys = "  gemini-pr-review:\n    enabled: false\n  gemini-review:\n    enabled: false\n"
    if anchor in text and "  gemini-pr-review:\n" not in text:
        config.write_text(text.replace(anchor, keys + anchor, 1), encoding="utf-8")
