#!/usr/bin/env python3
"""Verify that a workflow release tag is the intended, secure Git artifact."""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import difflib
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Iterable, Iterator, NamedTuple

import yaml

from scripts.workflow_catalog import (
    CatalogError,
    WorkflowCatalog,
    extract_caller_jobs,
    load_catalog,
    load_fleet_config,
)
from scripts.workflow_release_inventory import (
    CANONICALIZE_REVIEW_ACTION_ROOT,
    CANONICALIZE_REVIEW_HELPER_ROOT,
    PREPARE_REVIEW_DIFF_ACTION_ROOT,
    REVIEW_SCOPE_HELPER_ROOT,
    SETUP_GEMINI_AUTH_ROOT,
    release_paths_for,
    release_roots_for,
    release_supports_canonicalize_review,
    release_supports_prepare_review_diff,
    validate_release_listing,
)


CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
CLAUDE_CODE_ACTION = (
    "anthropics/claude-code-action@"
    "6bcfb8263aca9b0eab0aba20d96dddd74de2875f"
)
CACHE_ACTION = "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
UPLOAD_ARTIFACT_ACTION = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD_ARTIFACT_ACTION = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
OPENCODE_VERSION = "1.18.17"
OPENCODE_ARCHIVE_SHA256 = (
    "3f14a4c61c7f6b0d3b6d933d1d212e64e19683eba6fa453ad98e46303afe144a"
)
OPENCODE_REVIEW_RUN_SHA256 = (
    "9f1468128086b438cce0ce53fc20a9f0e02a14d581cd12b63f021c8c3a7620c6"
)
OPENCODE_AUTO_REVIEW_SHA256 = (
    "af209bfc73798a0cdda8d2f024f8392004c26814069cc244b80c298dd46b8d0f"
)
# These immutable annotated v1.45 patch releases predate format repair. Only their
# exact peeled commits may retain the legacy generic command; no new commit may opt in.
APPROVED_LEGACY_GENERIC_OPENCODE_RELEASES = frozenset(
    {
        (
            "v1.45",
            "9bfe6f4a9991d21ae95472e939d9e6b197174e9f",
        ),
        (
            "v1.45.1",
            "41131bb7843770259246e4125325a2ef4e95731f",
        ),
        (
            "v1.45.2",
            "abf5e65cf6188277d9984be062d0b069c82cf25f",
        ),
    }
)
SETUP_GEMINI_AUTH = (
    "jhw7500/automation/.github/actions/setup-gemini-auth@"
    "2254f13aab44585c78954d20749f4fb677a8c2f1"
)
SETUP_GEMINI_AUTH_REVIEW = "$/.github/actions/setup-gemini-auth"
APPROVED_V140_POLICY_FILES = (
    ".github/actions/setup-gemini-auth/action.yml",
    "scripts/workflow-catalog.json",
    "scripts/workflow-config.json",
    "examples/baseline-workflows/.github/workflow-config.yml",
    "examples/baseline-workflows/.github/workflows/auto-rereview-request.yml",
    "examples/baseline-workflows/.github/workflows/claude-code-review.yml",
    "examples/baseline-workflows/.github/workflows/claude.yml",
    "examples/baseline-workflows/.github/workflows/gemini-auto-review.yml",
    "examples/baseline-workflows/.github/workflows/gemini-chat.yml",
    "examples/baseline-workflows/.github/workflows/gemini-dispatch.yml",
    "examples/baseline-workflows/.github/workflows/gemini-invoke.yml",
    "examples/baseline-workflows/.github/workflows/gemini-issue-triage.yml",
    "examples/baseline-workflows/.github/workflows/gemini-pr-review.yml",
    "examples/baseline-workflows/.github/workflows/gemini-review.yml",
    "examples/baseline-workflows/.github/workflows/gemini-scheduled-triage.yml",
    "examples/baseline-workflows/.github/workflows/gemini-triage.yml",
    "examples/baseline-workflows/.github/workflows/opencode-auto-review.yml",
    "examples/baseline-workflows/.github/workflows/opencode.yml",
)
APPROVED_V140_POLICY_SHA256 = (
    "56d1672a70e2edc81b902894eba9b437c70fb7af54376e105bd1862637389642"
)
EXPECTED_GEMINI_MODE_INPUT = {
    "description": "Repository write authentication: github_app or github_token",
    "type": "string",
    "required": "true",
}
EXPECTED_GEMINI_APP_ID_INPUT = {
    "description": "GitHub App ID; used only when repo_write_auth is github_app",
    "type": "string",
    "required": "false",
    "default": "",
}
EXPECTED_GEMINI_PUBLISHER_APP_ID_INPUT = {
    "description": (
        "Trusted GitHub App ID for sticky publisher migration in github_token mode"
    ),
    "type": "string",
    "required": "false",
    "default": "",
}
EXPECTED_GEMINI_SECRETS = {
    "APP_PRIVATE_KEY": {
        "description": "GitHub App private key; used only for github_app mode",
        "required": "false",
    },
    "GEMINI_API_KEY": {
        "description": "Gemini API key",
        "required": "true",
    },
}
EXPECTED_GEMINI_AUTH_WITH = {
    "app-id": "${{ inputs.repo_write_auth == 'github_app' && inputs.app_id || '' }}",
    "private-key": (
        "${{ inputs.repo_write_auth == 'github_app' && "
        "secrets.APP_PRIVATE_KEY || '' }}"
    ),
    "fallback-token": (
        "${{ inputs.repo_write_auth == 'github_token' && github.token || '' }}"
    ),
}
EXPECTED_GEMINI_VALIDATION = {
    "name": "Validate repository-write auth",
    "shell": "bash",
    "env": {
        "MODE": "${{ inputs.repo_write_auth }}",
        "APP_ID": "${{ inputs.app_id }}",
        "APP_PRIVATE_KEY": "${{ secrets.APP_PRIVATE_KEY }}",
    },
    "run": (
        'case "$MODE" in\n'
        "  github_app)\n"
        '    test -n "$APP_ID" && test -n "$APP_PRIVATE_KEY" || {\n'
        "      echo 'github_app requires app_id and APP_PRIVATE_KEY' >&2\n"
        "      exit 1\n"
        "    }\n"
        "    ;;\n"
        "  github_token)\n"
        '    test -z "$APP_ID" && test -z "$APP_PRIVATE_KEY" || {\n'
        "      echo 'github_token forbids App credentials' >&2\n"
        "      exit 1\n"
        "    }\n"
        "    ;;\n"
        "  *)\n"
        '    echo "invalid repo_write_auth: $MODE" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n"
    ),
}
EXPECTED_GEMINI_AUTO_VALIDATION = {
    "name": "Validate repository-write auth",
    "shell": "bash",
    "env": {
        "MODE": "${{ inputs.repo_write_auth }}",
        "APP_ID": "${{ inputs.app_id }}",
        "PUBLISHER_APP_ID": "${{ inputs.publisher_app_id }}",
        "APP_PRIVATE_KEY": "${{ secrets.APP_PRIVATE_KEY }}",
    },
    "run": (
        'if [[ -n "$PUBLISHER_APP_ID" ]] && {\n'
        '  [[ ! "$PUBLISHER_APP_ID" =~ ^[1-9][0-9]{0,14}$ ]];\n'
        "}; then\n"
        "  echo 'publisher_app_id must be a positive decimal App ID of at most 15 digits' >&2\n"
        "  exit 1\n"
        "fi\n"
        'case "$MODE" in\n'
        "  github_app)\n"
        '    test -n "$APP_ID" && test -n "$APP_PRIVATE_KEY" || {\n'
        "      echo 'github_app requires app_id and APP_PRIVATE_KEY' >&2\n"
        "      exit 1\n"
        "    }\n"
        '    test -z "$PUBLISHER_APP_ID" || test "$PUBLISHER_APP_ID" = "$APP_ID" || {\n'
        "      echo 'github_app requires publisher_app_id to be empty or equal app_id' >&2\n"
        "      exit 1\n"
        "    }\n"
        "    ;;\n"
        "  github_token)\n"
        '    test -z "$APP_ID" && test -z "$APP_PRIVATE_KEY" || {\n'
        "      echo 'github_token forbids App credentials' >&2\n"
        "      exit 1\n"
        "    }\n"
        "    ;;\n"
        "  *)\n"
        '    echo "invalid repo_write_auth: $MODE" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n"
    ),
}
EXPECTED_SETUP_GEMINI_AUTH = {
    "name": "Setup Gemini Auth",
    "description": "Mint GitHub App token for Gemini workflows",
    "inputs": {
        "app-id": {"description": "GitHub App ID", "required": "false"},
        "private-key": {
            "description": "GitHub App Private Key",
            "required": "false",
        },
        "fallback-token": {
            "description": "Fallback token if App credentials not provided",
            "required": "false",
        },
    },
    "outputs": {
        "token": {
            "description": "Generated or fallback token",
            "value": "${{ steps.resolve.outputs.token }}",
        },
        "bot-login": {
            "description": "Comment publisher login",
            "value": "${{ steps.resolve.outputs.bot-login }}",
        },
    },
    "runs": {
        "using": "composite",
        "steps": [
            {
                "name": "Mint identity token",
                "id": "mint_token",
                "if": "${{ inputs.app-id != '' }}",
                "uses": (
                    "actions/create-github-app-token@"
                    "a8d616148505b5069dccd32f177bb87d7f39123b"
                ),
                "with": {
                    "app-id": "${{ inputs.app-id }}",
                    "private-key": "${{ inputs.private-key }}",
                    "permission-contents": "read",
                    "permission-issues": "write",
                    "permission-pull-requests": "write",
                },
            },
            {
                "name": "Resolve token",
                "id": "resolve",
                "shell": "bash",
                "env": {
                    "MINTED": "${{ steps.mint_token.outputs.token }}",
                    "APP_SLUG": "${{ steps.mint_token.outputs.app-slug }}",
                    "FALLBACK": "${{ inputs.fallback-token }}",
                },
                "run": (
                    'if [[ -n "$MINTED" ]]; then\n'
                    '  printf \'token=%s\\n\' "$MINTED" >> "$GITHUB_OUTPUT"\n'
                    '  printf \'bot-login=%s[bot]\\n\' "$APP_SLUG" >> "$GITHUB_OUTPUT"\n'
                    '  echo "::notice::Using GitHub App token"\n'
                    "else\n"
                    '  printf \'token=%s\\n\' "$FALLBACK" >> "$GITHUB_OUTPUT"\n'
                    "  printf 'bot-login=github-actions[bot]\\n' >> \"$GITHUB_OUTPUT\"\n"
                    '  echo "::notice::Using fallback token"\n'
                    "fi\n"
                ),
            },
        ],
    },
}
# v1.40-v1.45.x authenticated the historical action exactly as it was released.
# v1.46 adds the publisher identity used by sticky-state provenance checks; those
# action bytes must never be applied retroactively to historical releases.
EXPECTED_SETUP_GEMINI_AUTH_V145 = deepcopy(EXPECTED_SETUP_GEMINI_AUTH)
del EXPECTED_SETUP_GEMINI_AUTH_V145["outputs"]["bot-login"]
_legacy_setup_steps = EXPECTED_SETUP_GEMINI_AUTH_V145["runs"]["steps"]
del _legacy_setup_steps[1]["env"]["APP_SLUG"]
_legacy_setup_steps[1]["run"] = (
    'if [[ -n "$MINTED" ]]; then\n'
    '  printf \'token=%s\\n\' "$MINTED" >> "$GITHUB_OUTPUT"\n'
    '  echo "::notice::Using GitHub App token"\n'
    "else\n"
    '  printf \'token=%s\\n\' "$FALLBACK" >> "$GITHUB_OUTPUT"\n'
    '  echo "::notice::Using fallback token"\n'
    "fi\n"
)
del _legacy_setup_steps

EXPECTED_PREPARE_REVIEW_DIFF_ACTION = {
    "name": "Prepare review diff",
    "description": "Prepare a fail-closed full or incremental PR diff",
    "inputs": {
        "github-token": {"required": "true"},
        "pr-number": {"required": "true"},
        "previous-sha": {"required": "false", "default": ""},
        "previous-full-hash": {"required": "false", "default": ""},
        "context-lines": {"required": "false", "default": "3"},
        "output-directory": {"required": "true"},
    },
    "outputs": {
        "diff-ready": {"value": "${{ steps.prepare.outputs.diff_ready }}"},
        "diff-mode": {"value": "${{ steps.prepare.outputs.diff_mode }}"},
        "head-sha": {"value": "${{ steps.prepare.outputs.head_sha }}"},
        "full-diff-sha256": {
            "value": "${{ steps.prepare.outputs.full_diff_sha256 }}"
        },
        "unchanged-since-previous": {
            "value": "${{ steps.prepare.outputs.unchanged_since_previous }}"
        },
    },
    "runs": {
        "using": "composite",
        "steps": [
            {
                "id": "prepare",
                "shell": "bash",
                "env": {
                    "GH_TOKEN": "${{ inputs.github-token }}",
                    "PR_NUMBER": "${{ inputs.pr-number }}",
                    "PREVIOUS_SHA": "${{ inputs.previous-sha }}",
                    "PREVIOUS_FULL_HASH": "${{ inputs.previous-full-hash }}",
                    "CONTEXT_LINES": "${{ inputs.context-lines }}",
                    "OUTPUT_DIRECTORY": "${{ inputs.output-directory }}",
                },
                "run": (
                    'python3 "$GITHUB_ACTION_PATH/prepare_review_diff.py" '
                    '--repository "$GITHUB_REPOSITORY" '
                    '--pr-number "$PR_NUMBER" '
                    '--previous-sha "$PREVIOUS_SHA" '
                    '--previous-full-hash "$PREVIOUS_FULL_HASH" '
                    '--context-lines "$CONTEXT_LINES" '
                    '--full-output "$OUTPUT_DIRECTORY/review-full.diff" '
                    '--delta-output "$OUTPUT_DIRECTORY/review-delta.diff" '
                    '--manifest-output "$OUTPUT_DIRECTORY/review-scope.json" '
                    '--github-output "$GITHUB_OUTPUT"'
                ),
            }
        ],
    },
}
# v1.45.x wrote its prepared files into GITHUB_WORKSPACE. v1.46 introduces the
# required output-directory input so the final checkout cannot replace them.
EXPECTED_PREPARE_REVIEW_DIFF_ACTION_V145 = deepcopy(EXPECTED_PREPARE_REVIEW_DIFF_ACTION)
del EXPECTED_PREPARE_REVIEW_DIFF_ACTION_V145["inputs"]["output-directory"]
_legacy_prepare_step = EXPECTED_PREPARE_REVIEW_DIFF_ACTION_V145["runs"]["steps"][0]
del _legacy_prepare_step["env"]["OUTPUT_DIRECTORY"]
_legacy_prepare_step["run"] = (
    'python3 "$GITHUB_ACTION_PATH/prepare_review_diff.py" '
    '--repository "$GITHUB_REPOSITORY" '
    '--pr-number "$PR_NUMBER" '
    '--previous-sha "$PREVIOUS_SHA" '
    '--previous-full-hash "$PREVIOUS_FULL_HASH" '
    '--context-lines "$CONTEXT_LINES" '
    '--full-output "$GITHUB_WORKSPACE/review-full.diff" '
    '--delta-output "$GITHUB_WORKSPACE/review-delta.diff" '
    '--manifest-output "$GITHUB_WORKSPACE/review-scope.json" '
    '--github-output "$GITHUB_OUTPUT"'
)
del _legacy_prepare_step

EXPECTED_CANONICALIZE_REVIEW_ACTION = {
    "name": "Canonicalize review",
    "description": "Canonicalize evidenced Claude or Gemini review findings",
    "inputs": {
        "reviewer": {"required": "true"},
        "candidate-file": {"required": "true"},
        "canonical-file": {"required": "true"},
        "result-file": {"required": "true"},
        "scope-manifest": {"required": "true"},
        "selected-diff": {"required": "true"},
        "diff-mode": {"required": "true"},
        "previous-sha": {"required": "false", "default": ""},
        "previous-review-file": {"required": "false", "default": ""},
    },
    "outputs": {
        "document-valid": {
            "value": "${{ steps.canonicalize.outputs.document_valid }}"
        },
        "accepted-count": {
            "value": "${{ steps.canonicalize.outputs.accepted_count }}"
        },
        "filtered-count": {
            "value": "${{ steps.canonicalize.outputs.filtered_count }}"
        },
        "normalized-count": {
            "value": "${{ steps.canonicalize.outputs.normalized_count }}"
        },
        "filtered-max-severity": {
            "value": "${{ steps.canonicalize.outputs.filtered_max_severity }}"
        },
        "failure-reason": {
            "value": "${{ steps.canonicalize.outputs.failure_reason }}"
        },
    },
    "runs": {
        "using": "composite",
        "steps": [
            {
                "id": "canonicalize",
                "shell": "bash",
                "env": {
                    "REVIEWER": "${{ inputs.reviewer }}",
                    "CANDIDATE_FILE": "${{ inputs.candidate-file }}",
                    "CANONICAL_FILE": "${{ inputs.canonical-file }}",
                    "RESULT_FILE": "${{ inputs.result-file }}",
                    "SCOPE_MANIFEST": "${{ inputs.scope-manifest }}",
                    "SELECTED_DIFF": "${{ inputs.selected-diff }}",
                    "DIFF_MODE": "${{ inputs.diff-mode }}",
                    "PREVIOUS_SHA": "${{ inputs.previous-sha }}",
                    "PREVIOUS_REVIEW_FILE": "${{ inputs.previous-review-file }}",
                },
                "run": (
                    'python3 "$GITHUB_ACTION_PATH/canonicalize_review.py" '
                    '--reviewer "$REVIEWER" '
                    '--candidate-file "$CANDIDATE_FILE" '
                    '--canonical-file "$CANONICAL_FILE" '
                    '--result-file "$RESULT_FILE" '
                    '--scope-manifest "$SCOPE_MANIFEST" '
                    '--selected-diff "$SELECTED_DIFF" '
                    '--diff-mode "$DIFF_MODE" '
                    '--previous-sha "$PREVIOUS_SHA" '
                    '--previous-review-file "$PREVIOUS_REVIEW_FILE" '
                    '--repository-root "$GITHUB_WORKSPACE" '
                    '--expected-repository "$GITHUB_REPOSITORY" '
                    '--github-output "$GITHUB_OUTPUT"'
                ),
            }
        ],
    },
}
EXPECTED_CANONICALIZER_HARD_REASONS = frozenset(
    {
        "candidate_missing",
        "invalid_utf8",
        "candidate_oversize",
        "ambiguous_document",
        "scope_invalid",
        "canonicalizer_error",
    }
)
EXPECTED_CANONICALIZER_SOFT_REASONS = frozenset(
    {
        "invalid_anchor",
        "invalid_trigger_evidence",
        "invalid_severity",
        "invalid_impact_class",
        "missing_material_impact",
        "unsupported_performance_basis",
        "non_actionable_category",
        "unknown_prior_id",
        "duplicate_prior_binding",
        "missing_fix_anchor",
    }
)
EXPECTED_CANONICALIZE_REVIEW_HELPER_SHA256 = (
    "36babc0bc0159e7f1315cadca22ba8a7e1a8756bfd929637bab0fe1d21ed8d68"
)
EXPECTED_REVIEW_SCOPE_HELPER_SHA256 = (
    "68779c9038c31aa09a846b643bc0178b147798527e1a34ee5821ab539f10b19a"
)
EXPECTED_SCOPE_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent/automation-review-scope/home",
    "XDG_CONFIG_HOME": "/nonexistent/automation-review-scope/xdg",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/false",
    "SSH_ASKPASS": "/bin/false",
    "GIT_EXTERNAL_DIFF": "",
}
EXPECTED_CANONICALIZER_RECORDS = {
    "CanonicalizationRequest": (
        ("reviewer", "Literal['claude', 'gemini']"),
        ("candidate_file", "Path"),
        ("canonical_file", "Path"),
        ("result_file", "Path"),
        ("scope_manifest", "Path"),
        ("selected_diff", "Path"),
        ("repository_root", "Path"),
        ("diff_mode", "Literal['full', 'delta']"),
        ("previous_sha", "str"),
        ("previous_review_file", "Path | None"),
        ("expected_repository", "str"),
    ),
    "CandidateReason": (
        ("index", "int"),
        (
            "section",
            "Literal['New findings', 'Still open', 'Resolved', 'Retracted']",
        ),
        ("outcome", "Literal['filtered', 'normalized']"),
        ("reason", "str"),
        ("claimed_severity", "Literal['none', 'MEDIUM', 'HIGH', 'CRITICAL']"),
    ),
    "CanonicalizationResult": (
        ("document_valid", "bool"),
        ("accepted_count", "int"),
        ("filtered_count", "int"),
        ("normalized_count", "int"),
        ("filtered_max_severity", "Literal['none', 'MEDIUM', 'HIGH', 'CRITICAL']"),
        ("failure_reason", "str"),
        ("candidate_reasons", "tuple[CandidateReason, ...]"),
    ),
}
EXPECTED_SCOPE_RECORDS = {
    "SourceAnchor": (("path", "str"), ("line", "int")),
    "TriggerEvidence": (
        ("path", "str"),
        ("line", "int"),
        ("quote", "str"),
    ),
    "ReviewScope": (
        ("repository_root", "Path"),
        ("manifest", "ScopeManifest"),
        ("diff_mode", "Literal['full', 'delta']"),
        ("added_lines_by_path", "dict[str, dict[int, str]]"),
    ),
}
EXPECTED_CANONICALIZER_FUNCTIONS = {
    "stable_finding_id": (
        "def stable_finding_id(reviewer: str, anchor: SourceAnchor, severity: str, "
        "title: str) -> str"
    ),
    "canonicalize": (
        "def canonicalize(request: CanonicalizationRequest) -> CanonicalizationResult"
    ),
}
EXPECTED_SCOPE_FUNCTIONS = {
    "load_review_scope": (
        "def load_review_scope(repository_root: Path, manifest_path: Path, "
        "selected_diff_path: Path, *, diff_mode: Literal['full', 'delta'], "
        "previous_sha: str, expected_repository: str) -> ReviewScope"
    ),
}
EXPECTED_REVIEW_SCOPE_METHODS = {
    "validate_changed_anchor": (
        "def validate_changed_anchor(self, anchor: SourceAnchor) -> bool"
    ),
    "validate_fix_anchor": (
        "def validate_fix_anchor(self, anchor: SourceAnchor) -> bool"
    ),
    "validate_trigger": (
        "def validate_trigger(self, evidence: TriggerEvidence) -> bool"
    ),
}
class ManualGeminiContract(NamedTuple):
    step_name: str
    step_id: str
    number_name: str
    number_expression: str
    view_command: str
    permission_name: str
    output_prefix: str
    downstream_job: str


MANUAL_GEMINI_FETCH_CONTRACTS = {
    "gemini-issue-triage.yml": ManualGeminiContract(
        step_name="Fetch issue",
        step_id="issue",
        number_name="ISSUE_NUMBER",
        number_expression="${{ inputs.issue_number }}",
        view_command="gh issue view",
        permission_name="issues",
        output_prefix="issue",
        downstream_job="triage",
    ),
    "gemini-pr-review.yml": ManualGeminiContract(
        step_name="Fetch PR",
        step_id="pr",
        number_name="PR_NUMBER",
        number_expression="${{ inputs.pr_number }}",
        view_command="gh pr view",
        permission_name="pull-requests",
        output_prefix="pr",
        downstream_job="review",
    ),
}
GEMINI_AUTH_OUTPUT = "${{ steps.auth.outputs.token }}"
GITHUB_ACTIONS_PROVENANCE_TOKEN = "${{ github.token }}"
APPROVED_GEMINI_ACTIONS = frozenset(
    {
        CHECKOUT_ACTION,
        "actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea",
        "google-github-actions/run-gemini-cli@v0",
        "jhw7500/automation/.github/actions/check-workflow-enabled@v1.1",
        SETUP_GEMINI_AUTH,
    }
)
PREPARE_REVIEW_DIFF_ACTION = (
    f"$/{PREPARE_REVIEW_DIFF_ACTION_ROOT.path.parent.as_posix()}"
)
CANONICALIZE_REVIEW_ACTION = (
    f"$/{CANONICALIZE_REVIEW_ACTION_ROOT.path.parent.as_posix()}"
)
REVIEW_DIFF_DEPENDENCY_WORKFLOWS = (
    "claude-code-review.yml",
    "gemini-auto-review.yml",
    "opencode-auto-review.yml",
)
CANONICALIZE_REVIEW_WORKFLOWS = frozenset(
    {"claude-code-review.yml", "gemini-auto-review.yml"}
)
QUALITY_STATE_KEYS = (
    "accepted_count",
    "attempt_head",
    "attempt_status",
    "diff_mode",
    "filtered_count",
    "filtered_max_severity",
    "full_diff_sha256",
    "normalized_count",
    "pr",
    "quality_schema",
    "reviewer",
    "run_attempt",
    "run_id",
    "schema",
    "successful_head",
)
REVIEW_PUBLICATION_CONTRACTS = {
    "claude-code-review.yml": {
        "job": "claude-review",
        "collector": "Collect previous review context",
        "collector_id": "prepare-review-input",
        "provider": "Run Claude Code Review",
        "prompt_location": "with",
        "reviewer": "claude",
        "marker": "<!-- automation:claude-code-review:v3 -->",
        "v2_marker": "<!-- automation:claude-code-review:v2 -->",
        "raw": "claude-review.md",
        "canonical": "claude-review-canonical.md",
        "canonical_step": "Canonicalize Claude review",
        # Intentional whole-program v1.46 publication boundary. Any Upsert
        # program change requires an explicit verifier contract update.
        "upsert_sha256": (
            "d2f1d2eab1e974bf05f184406e854cfe0861ab4b863520a4189d987ceccf27cc"
        ),
        "bot_login": "github-actions[bot]",
        "workflow_prefix": (
            "jhw7500/automation/.github/workflows/claude-code-review.yml@"
        ),
        "previous_sha": "${{ steps.prepare-review-input.outputs.previous_sha }}",
        "upsert_if": (
            "${{ !cancelled() && steps.prepare-review-input.outcome == 'success' }}"
        ),
        "previous_file": (
            "${{ steps.prepare-review-input.outputs.previous_sha != '' && "
            "format('{0}/claude-previous-review.md', runner.temp) || '' }}"
        ),
        "reset": "reset-claude-artifacts",
    },
    "gemini-auto-review.yml": {
        "job": "gemini-review",
        "collector": "Get PR details",
        "collector_id": "pr-details",
        "provider": "Run Gemini Code Review",
        "prompt_location": "run",
        "reviewer": "gemini",
        "marker": "<!-- automation:gemini-auto-review:v3 -->",
        "v2_marker": "<!-- automation:gemini-auto-review:v2 -->",
        "raw": "gemini_review.md",
        "canonical": "gemini-review-canonical.md",
        "canonical_step": "Canonicalize Gemini review",
        "upsert_sha256": (
            "cc81b9e370c357366a384a059c9d6e1fe02065f085985f30532b92410c49c43d"
        ),
        "bot_login": "${{ steps.auth.outputs.bot-login }}",
        "auth_mode": "${{ inputs.repo_write_auth }}",
        "publisher_app_id": "${{ inputs.publisher_app_id }}",
        "workflow_prefix": (
            "jhw7500/automation/.github/workflows/gemini-auto-review.yml@"
        ),
        "previous_sha": "${{ steps.pr-details.outputs.previous_sha }}",
        "upsert_if": "${{ !cancelled() && steps.pr-details.outcome == 'success' }}",
        "previous_file": (
            "${{ steps.pr-details.outputs.previous_sha != '' && "
            "format('{0}/gemini-previous-review.md', runner.temp) || '' }}"
        ),
        "reset": "reset-gemini-artifacts",
    },
}
GIT_EXECUTABLE = "/usr/bin/git"
CANONICAL_AUTOMATION_REMOTE = "https://github.com/jhw7500/automation.git"
ACCEPTED_AUTOMATION_REMOTES = frozenset(
    {
        CANONICAL_AUTOMATION_REMOTE,
        "https://github.com/jhw7500/automation",
    }
)
HERMETIC_GIT_HOME = "/nonexistent/automation-workflow-release/home"
HERMETIC_GIT_XDG = "/nonexistent/automation-workflow-release/xdg"
OID = re.compile(r"[0-9a-f]{40}")
RELEASE_REF = re.compile(r"v[0-9]+(?:\.[0-9]+)+")
TAGGER_IDENT = re.compile(
    rb"(?P<name>[^\x00-\x1f\x7f<>]+) <[^\x00-\x1f\x7f<> ]+> "
    rb"(?P<timestamp>[0-9]+) "
    rb"(?P<sign>[+-])(?P<hour>[0-9]{2})(?P<minute>[0-9]{2})\Z"
)
MAX_GIT_METADATA_BYTES = 1024 * 1024


class ReleaseVerificationError(RuntimeError):
    """The requested release is absent, points elsewhere, or violates invariants."""


@dataclass(frozen=True)
class AnnotatedTag:
    ref: str
    tag_object: str
    commit: str


@dataclass(frozen=True)
class GitStorage:
    common_fd: int
    object_fd: int
    owner_uid: int


def git_child_env() -> dict[str, str]:
    """Return a fixed environment that excludes host and provider Git state."""

    return {
        "PATH": "/usr/bin:/bin",
        "HOME": HERMETIC_GIT_HOME,
        "XDG_CONFIG_HOME": HERMETIC_GIT_XDG,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "GCM_INTERACTIVE": "Never",
    }


_SECURE_FILESYSTEM_NAMES = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")


def _require_secure_filesystem() -> None:
    supported = (
        os.name == "posix"
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and hasattr(os, "geteuid")
        and all(hasattr(os, name) for name in _SECURE_FILESYSTEM_NAMES)
        and Path("/proc/self/fd").is_dir()
    )
    if not supported:
        raise ReleaseVerificationError("unsupported Git repository layout")


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_child_directory(
    directory_fd: int,
    name: str,
    *,
    expected_uid: int | None,
    missing_ok: bool = False,
) -> int | None:
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise ReleaseVerificationError("unsupported Git repository layout")
    descriptor: int | None = None
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ReleaseVerificationError("unsupported Git repository layout") from None
    except OSError:
        raise ReleaseVerificationError("unsupported Git repository layout") from None
    try:
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise OSError
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        opened = os.fstat(descriptor)
        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _directory_identity(before) != _directory_identity(opened)
            or _directory_identity(opened) != _directory_identity(after)
            or (expected_uid is not None and opened.st_uid != expected_uid)
        ):
            raise OSError
        return descriptor
    except OSError:
        pass
    if descriptor is not None:
        os.close(descriptor)
    raise ReleaseVerificationError("unsupported Git repository layout") from None


def _absolute_metadata_path(base: Path, raw: str) -> Path:
    if not raw or "\0" in raw:
        raise ReleaseVerificationError("unsupported Git repository layout")
    candidate = Path(raw)
    parts = candidate.parts
    parent_prefix = 0
    if candidate.is_absolute():
        if ".." in parts:
            raise ReleaseVerificationError("unsupported Git repository layout")
    else:
        while parent_prefix < len(parts) and parts[parent_prefix] == "..":
            parent_prefix += 1
        if ".." in parts[parent_prefix:]:
            raise ReleaseVerificationError("unsupported Git repository layout")
        candidate = base / candidate
    return Path(os.path.abspath(os.fspath(candidate)))


def _open_directory_path(path: Path, *, expected_uid: int | None) -> tuple[Path, int]:
    _require_secure_filesystem()
    if ".." in path.parts:
        raise ReleaseVerificationError("unsupported Git repository layout")
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor: int | None = None
    try:
        descriptor = os.open(
            "/", os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        for component in absolute.parts[1:]:
            child = _open_child_directory(
                descriptor, component, expected_uid=None
            )
            assert child is not None
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if expected_uid is not None and metadata.st_uid != expected_uid:
            raise OSError
        return absolute, descriptor
    except (OSError, ReleaseVerificationError):
        if descriptor is not None:
            os.close(descriptor)
        raise ReleaseVerificationError("unsupported Git repository layout") from None


def _read_metadata_at(
    directory_fd: int,
    name: str,
    *,
    maximum: int = MAX_GIT_METADATA_BYTES,
    expected_uid: int,
    missing_ok: bool = False,
) -> bytes | None:
    """Read one stable, owned regular metadata file without following names."""

    _require_secure_filesystem()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\0" in name
        or maximum < 0
    ):
        raise ReleaseVerificationError("unsupported Git repository metadata")
    descriptor: int | None = None
    try:
        named_before = os.stat(
            name, dir_fd=directory_fd, follow_symlinks=False
        )
    except OSError as exc:
        if missing_ok and exc.errno == errno.ENOENT:
            return None
        raise ReleaseVerificationError(
            "unsupported Git repository metadata"
        ) from None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
    except OSError:
        raise ReleaseVerificationError(
            "unsupported Git repository metadata"
        ) from None

    try:
        opened_before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or stat.S_ISLNK(named_before.st_mode)
            or opened_before.st_uid != expected_uid
            or opened_before.st_nlink != 1
            or opened_before.st_size > maximum
            or _metadata_identity(named_before)
            != _metadata_identity(opened_before)
        ):
            raise OSError
        chunks: list[bytes] = []
        length = 0
        while length <= maximum:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            length > maximum
            or length != opened_before.st_size
            or _metadata_identity(opened_before) != _metadata_identity(after)
            or _metadata_identity(after) != _metadata_identity(current)
        ):
            raise OSError
        return value
    except OSError:
        raise ReleaseVerificationError(
            "unsupported Git repository metadata"
        ) from None
    finally:
        os.close(descriptor)


def _one_metadata_line_at(
    directory_fd: int,
    name: str,
    *,
    expected_uid: int,
    missing_ok: bool = False,
) -> str | None:
    value = _read_metadata_at(
        directory_fd,
        name,
        maximum=4096,
        expected_uid=expected_uid,
        missing_ok=missing_ok,
    )
    if value is None:
        return None
    try:
        lines = value.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        lines = []
    if len(lines) != 1 or not lines[0]:
        raise ReleaseVerificationError("unsupported Git repository metadata")
    return lines[0]


def _entry_exists_at(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise ReleaseVerificationError("unsupported Git object storage") from None


def _unsupported_object_storage(
    git_fd: int, common_fd: int, object_fd: int, *, owner_uid: int
) -> bool:
    if _entry_exists_at(common_fd, "shallow") or _entry_exists_at(git_fd, "shallow"):
        return True
    opened: list[int] = []
    try:
        info_fd = _open_child_directory(
            object_fd, "info", expected_uid=owner_uid, missing_ok=True
        )
        if info_fd is not None:
            opened.append(info_fd)
            if _entry_exists_at(info_fd, "alternates") or _entry_exists_at(
                info_fd, "http-alternates"
            ):
                return True
        pack_fd = _open_child_directory(
            object_fd, "pack", expected_uid=owner_uid, missing_ok=True
        )
        if pack_fd is not None:
            opened.append(pack_fd)
            if any(name.endswith(".promisor") for name in os.listdir(pack_fd)):
                return True
        return False
    except (OSError, ReleaseVerificationError):
        raise ReleaseVerificationError("unsupported Git object storage") from None
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


@contextmanager
def git_storage(repo: Path) -> Iterator[GitStorage]:
    """Pin a normal or linked-worktree object store without invoking Git."""

    owner_uid = os.geteuid() if hasattr(os, "geteuid") else -1
    descriptors: list[int] = []
    try:
        root, root_fd = _open_directory_path(repo, expected_uid=owner_uid)
        descriptors.append(root_fd)
        try:
            dot_git = os.stat(".git", dir_fd=root_fd, follow_symlinks=False)
        except OSError:
            raise ReleaseVerificationError(
                "unsupported Git repository layout"
            ) from None
        if stat.S_ISDIR(dot_git.st_mode) and not stat.S_ISLNK(dot_git.st_mode):
            opened = _open_child_directory(
                root_fd, ".git", expected_uid=owner_uid
            )
            assert opened is not None
            git_dir = root / ".git"
            git_fd = opened
        elif stat.S_ISREG(dot_git.st_mode) and not stat.S_ISLNK(dot_git.st_mode):
            pointer = _one_metadata_line_at(
                root_fd, ".git", expected_uid=owner_uid
            )
            assert pointer is not None
            if not pointer.startswith("gitdir: "):
                raise ReleaseVerificationError("unsupported Git repository layout")
            git_dir = _absolute_metadata_path(
                root, pointer.removeprefix("gitdir: ")
            )
            if git_dir.parent.name != "worktrees":
                raise ReleaseVerificationError("unsupported Git repository layout")
            git_dir, git_fd = _open_directory_path(
                git_dir, expected_uid=owner_uid
            )
        else:
            raise ReleaseVerificationError("unsupported Git repository layout")
        descriptors.append(git_fd)

        common_value = _one_metadata_line_at(
            git_fd, "commondir", expected_uid=owner_uid, missing_ok=True
        )
        if common_value is None:
            common_dir = git_dir
            common_fd = os.dup(git_fd)
        else:
            common_dir = _absolute_metadata_path(git_dir, common_value)
            if common_dir != git_dir.parent.parent:
                raise ReleaseVerificationError("unsupported Git repository layout")
            common_dir, common_fd = _open_directory_path(
                common_dir, expected_uid=owner_uid
            )
        descriptors.append(common_fd)

        opened = _open_child_directory(
            common_fd, "objects", expected_uid=owner_uid
        )
        assert opened is not None
        object_fd = opened
        descriptors.append(object_fd)
        if os.fstat(object_fd).st_dev != os.fstat(common_fd).st_dev:
            raise ReleaseVerificationError("unsupported Git repository layout")
        if _unsupported_object_storage(
            git_fd, common_fd, object_fd, owner_uid=owner_uid
        ):
            raise ReleaseVerificationError("unsupported Git object storage")
        yield GitStorage(common_fd, object_fd, owner_uid)
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


@contextmanager
def _raw_git_environment(
    repo: Path,
) -> Iterator[tuple[dict[str, str], tuple[int, ...]]]:
    with git_storage(repo) as storage:
        with tempfile.TemporaryDirectory(prefix="workflow-release-git-") as temporary:
            isolated = Path(temporary) / "git"
            (isolated / "objects").mkdir(parents=True)
            (isolated / "refs").mkdir()
            (isolated / "HEAD").write_text(
                "ref: refs/heads/unborn\n", encoding="ascii"
            )
            yield (
                {
                    **git_child_env(),
                    "GIT_DIR": str(isolated),
                    "GIT_OBJECT_DIRECTORY": f"/proc/self/fd/{storage.object_fd}",
                    "GIT_NO_REPLACE_OBJECTS": "1",
                    "GIT_OPTIONAL_LOCKS": "0",
                },
                (storage.object_fd,),
            )


def remote_git_env() -> dict[str, str]:
    """Restrict remote verification to unauthenticated public HTTPS."""

    return {
        **git_child_env(),
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_CEILING_DIRECTORIES": "/",
    }


def _git_object_frame(repo: Path, oid: str) -> bytes:
    result: subprocess.CompletedProcess[bytes] | None = None
    try:
        with _raw_git_environment(repo) as (environment, pass_fds):
            result = subprocess.run(
                [GIT_EXECUTABLE, "cat-file", "--batch"],
                cwd="/",
                input=f"{oid}\n".encode("ascii"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                pass_fds=pass_fds,
            )
    except (OSError, UnicodeEncodeError, ValueError):
        pass
    if result is None or result.returncode != 0:
        raise ReleaseVerificationError("Git object is invalid") from None
    return result.stdout


def read_git_object(repo: Path, oid: str, expected_type: str) -> bytes:
    """Return one object only after authenticating its standard Git SHA-1 name."""

    if not _valid_oid(oid) or expected_type not in {"tag", "commit", "tree", "blob"}:
        raise ReleaseVerificationError("Git object is invalid")
    try:
        header, separator, framed = _git_object_frame(repo, oid).partition(b"\n")
        raw_oid, raw_type, raw_size = header.split(b" ")
        object_oid = raw_oid.decode("ascii")
        object_type = raw_type.decode("ascii")
        rendered_size = raw_size.decode("ascii")
        if (
            separator != b"\n"
            or object_oid != oid
            or object_type != expected_type
            or re.fullmatch(r"0|[1-9][0-9]*", rendered_size) is None
        ):
            raise ValueError
        size = int(rendered_size)
        if len(framed) != size + 1 or framed[-1:] != b"\n":
            raise ValueError
        payload = framed[:size]
        digest = hashlib.sha1(
            f"{object_type} {len(payload)}\0".encode("ascii") + payload
        ).hexdigest()
        if digest != oid:
            raise ValueError
        return payload
    except (UnicodeDecodeError, ValueError):
        raise ReleaseVerificationError("Git object is invalid") from None


@dataclass
class GitObjectReader:
    """Shared checksum gate for release-owned Git object payloads."""

    repo: Path
    _cache: dict[tuple[str, str], bytes] = field(default_factory=dict)

    def read(self, oid: str, expected_type: str) -> bytes:
        key = (oid, expected_type)
        if key not in self._cache:
            self._cache[key] = read_git_object(self.repo, oid, expected_type)
        return self._cache[key]


@dataclass(frozen=True)
class GitTreeEntry:
    mode: str
    name: bytes
    oid: str

    @property
    def object_type(self) -> str:
        if self.mode == "40000":
            return "tree"
        if self.mode == "160000":
            return "commit"
        return "blob"


@dataclass(frozen=True)
class GitTreeFile:
    path: PurePosixPath
    mode: str
    oid: str
    object_type: str


_TREE_ENTRY_MODES = frozenset({"40000", "100644", "100755", "120000", "160000"})


def _linked_oid(payload: bytes, prefix: bytes) -> str:
    first, separator, _remainder = payload.partition(b"\n")
    if separator != b"\n" or not first.startswith(prefix):
        raise ReleaseVerificationError("Git object is invalid")
    try:
        oid = first.removeprefix(prefix).decode("ascii")
    except UnicodeDecodeError:
        raise ReleaseVerificationError("Git object is invalid") from None
    if not _valid_oid(oid):
        raise ReleaseVerificationError("Git object is invalid")
    return oid


def _tag_commit_oid(payload: bytes, requested_ref: str) -> str:
    """Parse the fail-closed release contract: one annotated tag -> one commit."""

    if RELEASE_REF.fullmatch(requested_ref) is None:
        raise ReleaseVerificationError("Git object is invalid")
    try:
        expected_tag = requested_ref.encode("ascii")
    except UnicodeEncodeError:
        raise ReleaseVerificationError("Git object is invalid") from None
    header, separator, _message = payload.partition(b"\n\n")
    if separator != b"\n\n":
        raise ReleaseVerificationError("Git object is invalid")
    parsed: list[tuple[bytes, bytes]] = []
    for line in header.split(b"\n"):
        if line.startswith(b" "):
            raise ReleaseVerificationError("Git object is invalid")
        key, space, value = line.partition(b" ")
        if (
            space != b" "
            or not key
            or not value
            or any(byte < 0x21 or byte > 0x7E for byte in key)
        ):
            raise ReleaseVerificationError("Git object is invalid")
        parsed.append((key, value))
    if [key for key, _value in parsed] != [b"object", b"type", b"tag", b"tagger"]:
        raise ReleaseVerificationError("Git object is invalid")
    tagger = TAGGER_IDENT.fullmatch(parsed[3][1])
    if (
        tagger is None
        or not tagger.group("name").strip(b" ")
        or (
            len(tagger.group("timestamp")) > 1
            and tagger.group("timestamp").startswith(b"0")
        )
        or int(tagger.group("timestamp")) > 2**63 - 1
        or int(tagger.group("hour")) > 23
        or int(tagger.group("minute")) > 59
    ):
        raise ReleaseVerificationError("Git object is invalid")
    protected = {
        key: [value for observed, value in parsed if observed == key]
        for key in (b"object", b"type", b"tag")
    }
    if (
        len(protected[b"object"]) != 1
        or len(protected[b"type"]) != 1
        or len(protected[b"tag"]) != 1
        or protected[b"type"][0] != b"commit"
        or protected[b"tag"][0] != expected_tag
        or parsed[:3]
        != [
            (b"object", protected[b"object"][0]),
            (b"type", b"commit"),
            (b"tag", expected_tag),
        ]
    ):
        raise ReleaseVerificationError("Git object is invalid")
    oid = protected[b"object"][0]
    try:
        decoded = oid.decode("ascii")
    except UnicodeDecodeError:
        raise ReleaseVerificationError("Git object is invalid") from None
    if not _valid_oid(decoded):
        raise ReleaseVerificationError("Git object is invalid")
    return decoded


def _parse_tree(payload: bytes) -> tuple[GitTreeEntry, ...]:
    entries: list[GitTreeEntry] = []
    seen: set[bytes] = set()
    cursor = 0
    previous_key: bytes | None = None
    try:
        while cursor < len(payload):
            space = payload.index(b" ", cursor)
            nul = payload.index(b"\0", space + 1)
            mode = payload[cursor:space].decode("ascii")
            name = payload[space + 1 : nul]
            oid_end = nul + 21
            ordering_key = name + (b"/" if mode == "40000" else b"\0")
            if (
                mode not in _TREE_ENTRY_MODES
                or not name
                or name in {b".", b".."}
                or b"/" in name
                or name in seen
                or oid_end > len(payload)
                or (previous_key is not None and ordering_key <= previous_key)
            ):
                raise ValueError
            oid = payload[nul + 1 : oid_end].hex()
            if not _valid_oid(oid):
                raise ValueError
            seen.add(name)
            entries.append(GitTreeEntry(mode, name, oid))
            previous_key = ordering_key
            cursor = oid_end
    except (UnicodeDecodeError, ValueError):
        raise ReleaseVerificationError("Git tree is invalid") from None
    return tuple(entries)


def _release_path(path: str | PurePosixPath) -> PurePosixPath:
    rendered = str(path)
    parsed = PurePosixPath(rendered)
    if (
        not parsed.parts
        or parsed.is_absolute()
        or ".." in parsed.parts
        or rendered != parsed.as_posix()
    ):
        raise ReleaseVerificationError("release path is invalid")
    return parsed


class VerifiedCommitTree:
    """Python traversal of one authenticated commit and its selected tree paths."""

    def __init__(self, reader: GitObjectReader, commit: str) -> None:
        self.reader = reader
        self.commit = commit
        commit_payload = reader.read(commit, "commit")
        self.root_oid = _linked_oid(commit_payload, b"tree ")
        self._trees: dict[str, tuple[GitTreeEntry, ...]] = {}
        self._entries(self.root_oid)

    @classmethod
    def open(cls, repo: Path, commit: str) -> VerifiedCommitTree:
        if not _valid_oid(commit):
            raise ReleaseVerificationError("commit has an invalid Git identity")
        return cls(GitObjectReader(repo), commit)

    def _entries(self, oid: str) -> tuple[GitTreeEntry, ...]:
        if oid not in self._trees:
            self._trees[oid] = _parse_tree(self.reader.read(oid, "tree"))
        return self._trees[oid]

    def entry(self, path: str | PurePosixPath) -> GitTreeEntry | None:
        release_path = _release_path(path)
        tree_oid = self.root_oid
        for index, component in enumerate(release_path.parts):
            raw_component = component.encode("utf-8")
            entry = next(
                (
                    candidate
                    for candidate in self._entries(tree_oid)
                    if candidate.name == raw_component
                ),
                None,
            )
            if entry is None:
                return None
            if index == len(release_path.parts) - 1:
                return entry
            if entry.object_type != "tree":
                return None
            tree_oid = entry.oid
        return None

    def read_file(self, path: str | PurePosixPath) -> bytes:
        entry = self.entry(path)
        if entry is None or entry.object_type != "blob":
            raise ReleaseVerificationError("release file is unavailable")
        return self.reader.read(entry.oid, "blob")

    def read_text(self, path: str | PurePosixPath) -> str:
        try:
            return self.read_file(path).decode("utf-8")
        except UnicodeDecodeError:
            raise ReleaseVerificationError("release file contains invalid text") from None

    def _walk_tree(
        self, tree_oid: str, parent: PurePosixPath
    ) -> Iterator[GitTreeFile]:
        for entry in self._entries(tree_oid):
            try:
                component = entry.name.decode("utf-8")
            except UnicodeDecodeError:
                raise ReleaseVerificationError("Git tree is invalid") from None
            path = parent / component
            if entry.object_type == "tree":
                yield from self._walk_tree(entry.oid, path)
            else:
                yield GitTreeFile(path, entry.mode, entry.oid, entry.object_type)

    def files(self, path: str | PurePosixPath) -> tuple[GitTreeFile, ...]:
        release_path = _release_path(path)
        entry = self.entry(release_path)
        if entry is None:
            return ()
        if entry.object_type == "tree":
            return tuple(self._walk_tree(entry.oid, release_path))
        return (
            GitTreeFile(release_path, entry.mode, entry.oid, entry.object_type),
        )

    def listing(self, paths: Iterable[str | PurePosixPath]) -> bytes:
        records: list[bytes] = []
        for path in paths:
            for entry in self.files(path):
                kind = entry.object_type
                mode = "040000" if entry.mode == "40000" else entry.mode
                records.append(
                    f"{mode} {kind} {entry.oid}\t{entry.path.as_posix()}\0".encode(
                        "utf-8"
                    )
                )
        return b"".join(records)


def remote_git(url: str, *refs: str) -> str:
    """Read tag refs from the one public automation endpoint without host config."""

    if url != CANONICAL_AUTOMATION_REMOTE:
        raise ReleaseVerificationError(
            "remote must be the canonical public HTTPS automation endpoint"
        )
    result: subprocess.CompletedProcess[str] | None = None
    try:
        result = subprocess.run(
            [GIT_EXECUTABLE, "ls-remote", "--tags", url, *refs],
            cwd="/",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=remote_git_env(),
        )
    except (OSError, ValueError):
        pass
    if result is None:
        raise ReleaseVerificationError(
            "Remote Git command failed (rc=unavailable)"
        ) from None
    if result.returncode != 0:
        raise ReleaseVerificationError(
            f"Remote Git command failed (rc={result.returncode})"
        ) from None
    return result.stdout


def _valid_oid(value: str) -> bool:
    return OID.fullmatch(value) is not None


def read_tag_oid(repo: Path, ref: str) -> str:
    """Read one version tag ref directly, without loading any Git configuration."""

    if RELEASE_REF.fullmatch(ref) is None:
        raise ReleaseVerificationError("invalid release ref")
    with git_storage(repo) as storage:
        opened: list[int] = []
        try:
            loose_value: str | None = None
            refs_fd = _open_child_directory(
                storage.common_fd,
                "refs",
                expected_uid=storage.owner_uid,
                missing_ok=True,
            )
            if refs_fd is not None:
                opened.append(refs_fd)
                tags_fd = _open_child_directory(
                    refs_fd,
                    "tags",
                    expected_uid=storage.owner_uid,
                    missing_ok=True,
                )
                if tags_fd is not None:
                    opened.append(tags_fd)
                    loose_value = _one_metadata_line_at(
                        tags_fd,
                        ref,
                        expected_uid=storage.owner_uid,
                        missing_ok=True,
                    )
            if loose_value is not None:
                if not _valid_oid(loose_value):
                    raise ReleaseVerificationError(
                        "release tag has an invalid Git identity"
                    )
                return loose_value

            packed = _read_metadata_at(
                storage.common_fd,
                "packed-refs",
                maximum=16 * 1024 * 1024,
                expected_uid=storage.owner_uid,
                missing_ok=True,
            )
            if packed is None:
                raise ReleaseVerificationError(
                    "release tag identity is unavailable"
                )
            lines = packed.decode("ascii").splitlines()
        except UnicodeDecodeError:
            raise ReleaseVerificationError(
                "release tag identity is unavailable"
            ) from None
        except ReleaseVerificationError as exc:
            if "invalid Git identity" in str(exc):
                raise
            raise ReleaseVerificationError(
                "release tag identity is unavailable"
            ) from None
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)
    target = f"refs/tags/{ref}"
    matches: list[str] = []
    for line in lines:
        if not line or line.startswith(("#", "^")):
            continue
        fields = line.split(" ")
        if len(fields) == 2 and fields[1] == target:
            matches.append(fields[0])
    if len(matches) != 1 or not _valid_oid(matches[0]):
        raise ReleaseVerificationError("release tag identity is unavailable")
    return matches[0]


def resolve_commit(repo: Path, revision: str) -> str:
    if not _valid_oid(revision):
        raise ReleaseVerificationError("commit has an invalid Git identity")
    try:
        read_git_object(repo, revision, "commit")
    except ReleaseVerificationError:
        raise ReleaseVerificationError("commit has an invalid Git identity") from None
    return revision


def resolve_annotated_tag(repo: Path, ref: str) -> AnnotatedTag:
    tag_object = read_tag_oid(repo, ref)
    try:
        payload = read_git_object(repo, tag_object, "tag")
        commit = _tag_commit_oid(payload, ref)
        read_git_object(repo, commit, "commit")
    except ReleaseVerificationError:
        raise ReleaseVerificationError(f"release {ref} must be an annotated tag")
    if not all(_valid_oid(value) for value in (tag_object, commit)):
        raise ReleaseVerificationError(f"release {ref} has an invalid Git identity")
    return AnnotatedTag(ref, tag_object, commit)


def assert_tag_unchanged(repo: Path, tag: AnnotatedTag) -> None:
    try:
        current = read_tag_oid(repo, tag.ref)
    except ReleaseVerificationError as exc:
        raise ReleaseVerificationError(
            f"tag {tag.ref} changed during verification"
        ) from exc
    if current != tag.tag_object:
        raise ReleaseVerificationError(f"tag {tag.ref} changed during verification")


def _direct_origin_urls(repo: Path) -> list[str]:
    """Parse only the literal origin URL in the common config; never follow includes."""

    with git_storage(repo) as storage:
        try:
            config = _read_metadata_at(
                storage.common_fd,
                "config",
                expected_uid=storage.owner_uid,
            )
            assert config is not None
            lines = config.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            raise ReleaseVerificationError(
                "origin configuration is invalid"
            ) from None
    in_origin = False
    urls: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("["):
            in_origin = (
                re.fullmatch(
                    r'\[remote\s+"origin"\](?:\s*[#;].*)?',
                    stripped,
                    flags=re.IGNORECASE,
                )
                is not None
            )
            continue
        if not in_origin:
            continue
        match = re.fullmatch(r"url\s*=\s*(\S+)", stripped, flags=re.IGNORECASE)
        if match is not None:
            urls.append(match.group(1))
    return urls


def _canonical_remote_url(repo: Path, remote: str) -> str:
    if remote != "origin":
        raise ReleaseVerificationError(
            "remote verification supports only origin"
        )
    try:
        configured = _direct_origin_urls(repo)
    except ReleaseVerificationError:
        configured = []
    if len(configured) != 1 or configured[0] not in ACCEPTED_AUTOMATION_REMOTES:
        raise ReleaseVerificationError(
            "origin must be the canonical public HTTPS automation remote"
        ) from None
    return CANONICAL_AUTOMATION_REMOTE


def verify_remote_tag(repo: Path, remote: str, tag: AnnotatedTag) -> None:
    url = _canonical_remote_url(repo, remote)
    result = remote_git(
        url,
        f"refs/tags/{tag.ref}",
        f"refs/tags/{tag.ref}^{{}}",
    )
    refs: dict[str, list[str]] = {}
    for line in result.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise ReleaseVerificationError(
                f"remote tag {tag.ref} returned an ambiguous identity"
            )
        sha, name = fields
        refs.setdefault(name, []).append(sha)
    direct = refs.get(f"refs/tags/{tag.ref}", [])
    peeled = refs.get(f"refs/tags/{tag.ref}^{{}}", [])
    if len(direct) != 1 or len(peeled) != 1:
        raise ReleaseVerificationError(
            f"remote tag {tag.ref} must be one annotated tag with one peeled commit"
        )
    if direct[0] != tag.tag_object or peeled[0] != tag.commit:
        raise ReleaseVerificationError(
            f"remote tag {tag.ref} identity mismatch: tag object {direct[0]} "
            f"(expected {tag.tag_object}), commit {peeled[0]} "
            f"(expected commit {tag.commit})"
        )


def _opencode_review_run_sha256(run_script: str) -> str:
    """Authenticate candidate-tool creation, mutation boundaries, and invocations."""
    return hashlib.sha256(run_script.encode("utf-8")).hexdigest()


def verify_opencode_runtime(
    job: dict,
    step_name: str,
    workflow_name: str,
    *,
    generic_run: bool = False,
    allow_legacy_generic: bool = False,
    workflow_sha256: str | None = None,
) -> dict:
    """Require a digest-verified CLI and the workflow-specific command boundary."""
    try:
        cache = next(
            item
            for item in job["steps"]
            if item.get("name") == "Cache pinned OpenCode CLI archive"
        )
        install = next(
            item
            for item in job["steps"]
            if item.get("name") == "Install pinned OpenCode CLI"
        )
        run_step = next(item for item in job["steps"] if item.get("name") == step_name)
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError(
            f"{workflow_name} pinned OpenCode runtime structure is missing"
        ) from exc

    job_env = job.get("env", {})
    install_script = install.get("run", "")
    expected_url = (
        "releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz"
    )
    run_script = run_step.get("run", "")
    run_env = run_step.get("env", {})
    legacy_generic_contract = (
        "opencode run --model zai-coding-plan/glm-4.7 --format json "
        "--file review-full.diff --file review-scope.json"
    ) in run_script
    format_sequence = (
        'run_opencode "$initial_prompt" "$RUNNER_TEMP/opencode-review.jsonl"',
        'if ! candidate_outer_format_valid "$candidate_dir/review.md"; then',
        '"$repair_prompt" "$RUNNER_TEMP/opencode-format-repair.jsonl"',
        'if ! candidate_outer_format_valid "$candidate_dir/review-repaired.md"; then',
        'echo "OpenCode format repair still violates the required outer grammar" >&2',
        "exit 1",
        'if ! initial_signature="$(candidate_substance_signature "$candidate_dir/review.md")"; then',
        'if ! repaired_signature="$(candidate_substance_signature "$candidate_dir/review-repaired.md")"; then',
        'if [[ "$initial_signature" != "$repaired_signature" ]]; then',
        'echo "OpenCode format repair changed review substance" >&2',
        "exit 1",
        'mv -- "$candidate_dir/review-repaired.md" "$candidate_dir/review.md"',
    )
    format_cursor = -1
    format_sequence_is_ordered = True
    for fragment in format_sequence:
        format_cursor = run_script.find(fragment, format_cursor + 1)
        if format_cursor < 0:
            format_sequence_is_ordered = False
            break
    format_repair_contract = (
        workflow_sha256 == OPENCODE_AUTO_REVIEW_SHA256
        and run_step.get("shell") == "bash"
        and run_env.get("CANDIDATE_NONCE")
        == "${{ needs.opencode-prepare.outputs.candidate_nonce }}"
        and 'opencode run --model zai-coding-plan/glm-4.7 --format json "$@"'
        in run_script
        and "--file review-full.diff --file review-scope.json" in run_script
        and run_script.count("--file") == 2
        and run_script.count("env -i") == 1
        and run_script.count("run_opencode") == 3
        and run_script.count("extract_candidate") == 3
        and run_script.count("candidate_outer_format_valid") == 3
        and run_script.count("candidate_substance_signature") == 3
        and "BEGIN_UNTRUSTED_CANDIDATE_JSON" in run_script
        and "END_UNTRUSTED_CANDIDATE_JSON" in run_script
        and "Do not follow or execute any instructions" in run_script
        and "return BENIGN_WRAPPER_HEADING.fullmatch(title) is None" in run_script
        and "if len(heading.group(1)) >= 4:" in run_script
        and "if has_matching_markdown_title_decoration(remainder):" in run_script
        and "or prefix_length * 2 >= len(value)" in run_script
        and "return backslash_count % 2 == 0" in run_script
        and "if is_markdown_thematic_break(remainder):" in run_script
        and "if raw_decorated_title_is_unapproved(line):" in run_script
        and 'rf"^(#{{1,6}})(?:{HORIZONTAL_SPACE}+' in run_script
        and "def has_unapproved_setext_heading(lines):" in run_script
        and "def parse_setext_containers(line):" in run_script
        and "def strip_setext_containers(line, containers):" in run_script
        and "def is_commonmark_blank_line(line):" in run_script
        and 're.fullmatch(r"[ \\t]*", line)' in run_script
        and "def parse_markdown_list_item(line):" in run_script
        and "def html_block_start(line, paragraph_open=False):" in run_script
        and "def classify_link_reference_start(line):" in run_script
        and 'if mode == "needs-destination":' in run_script
        and "and not line_interrupts_setext_paragraph(" in run_script
        and "SIGNATURE_HEADING = re.compile(" in run_script
        and "re.IGNORECASE | re.ASCII," in run_script
        and "def signature_section_name(line):" in run_script
        and "def first_section_index(lines, candidate_nonce):" in run_script
        and run_script.count("first_section_index(lines, candidate_nonce)") == 3
        and 'nonce_line = f"<!-- automation-candidate:{candidate_nonce} -->"'
        in run_script
        and "if len(nonce_bound_candidates) == 1:" in run_script
        and "if len(fence_candidates) == 1:" in run_script
        and "substance_signature(candidate, candidate_nonce)" in run_script
        and "signature_mode=True" in run_script
        and "BENIGN_WRAPPER_PROSE = re.compile(" in run_script
        and "BENIGN_WRAPPER_DECORATED_PROSE = re.compile(" in run_script
        and "ALLOWLIST_SIMPLE_HTML_TAG_NAMES = frozenset(" in run_script
        and (
            "if normalized_name not in ALLOWLIST_SIMPLE_HTML_TAG_NAMES:"
            in run_script
        )
        and "def wrapper_indented_code_block_end(" in run_script
        and "def is_benign_wrapper_prose_line(line):" in run_script
        and "def wrapper_line_closes_setext_paragraph(" in run_script
        and run_script.count("wrapper_line_closes_setext_paragraph(") == 4
        and "def wrapper_setext_underline_indices(lines):" in run_script
        and run_script.count("wrapper_setext_underline_indices(") == 2
        and "def has_unapproved_plain_wrapper_prose(lines):" in run_script
        and run_script.count("has_unapproved_plain_wrapper_prose(") == 3
        and "if not is_benign_wrapper_prose_line(lines[index]):" in run_script
        and "has_unapproved_plain_wrapper_prose(suffix)" in run_script
        and "has_unapproved_plain_wrapper_prose(prefix)" in run_script
        and "def has_unapproved_markdown_list_item(lines):" in run_script
        and "def is_benign_wrapper_list_item(content):" in run_script
        and "def benign_list_item_has_unapproved_continuation(" in run_script
        and "if benign_list_item_has_unapproved_continuation(" in run_script
        and "expanded = continuation.expandtabs(4)" in run_script
        and "if saw_blank:" in run_script
        and "def list_item_fenced_code_end(" in run_script
        and "def wrapper_literal_block_end(" in run_script
        and "incomplete_html_type in (1, 2, 3, 4, 5)" in run_script
        and 'incomplete_fence[1].strip(" \\t")' in run_script
        and 'opening_fence[1].strip(" \\t")' in run_script
        and "if index in setext_underline_indices:" in run_script
        and "has_unapproved_markdown_list_item(suffix)" in run_script
        and "has_unapproved_markdown_list_item(prefix)" in run_script
        and 'if re.match(r"^<![A-Z]", content) is not None:' in run_script
        and "MARKDOWN_EMPTY_LIST_ITEM.fullmatch(remainder) is not None" in run_script
        and run_script.count(
            "or MARKDOWN_EMPTY_LIST_ITEM.fullmatch(remainder) is not None"
        )
        == 2
        and "line = raw_line.expandtabs(4)" in run_script
        and "has_unapproved_setext_heading(suffix)" in run_script
        and "has_unapproved_setext_heading(prefix)" in run_script
        and 'remainder = line.strip()' in run_script
        and 'unicodedata.category(character) == "Zs"' in run_script
        and "normalize_wrapper_allowlist_text(" in run_script
        and _opencode_review_run_sha256(run_script) == OPENCODE_REVIEW_RUN_SHA256
        and format_sequence_is_ordered
    )
    selected_generic_contract = format_repair_contract or (
        allow_legacy_generic and legacy_generic_contract
    )
    generic_contract = (
        "opencode github run" not in run_script
        and selected_generic_contract
        and "map(fromjson)" in run_script
        and "fromjson?" not in run_script
        and "else last end" in run_script
        and run_env.get("OPENCODE_PURE") == "true"
        and run_env.get("OPENCODE_DISABLE_PROJECT_CONFIG") == "true"
        and run_env.get("OPENCODE_CONFIG_CONTENT")
        == '{"share":"disabled","snapshot":false,"permission":{"*":"deny"}}'
        and not {"GITHUB_TOKEN", "GH_TOKEN", "USE_GITHUB_TOKEN"}
        & set(run_env)
    )
    github_contract = (
        run_script == "opencode github run"
        and run_step.get("env", {}).get("USE_GITHUB_TOKEN") == "true"
    )
    runtime_is_pinned = (
        job_env.get("OPENCODE_VERSION") == OPENCODE_VERSION
        and job_env.get("OPENCODE_ARCHIVE_SHA256") == OPENCODE_ARCHIVE_SHA256
        and cache.get("uses") == CACHE_ACTION
        and expected_url in install_script
        and "sha256sum --check -" in install_script
        and '"$install_dir/opencode" --version' in install_script
        and (generic_contract if generic_run else github_contract)
        and (generic_run or run_step.get("env", {}).get("MODEL") == "zai-coding-plan/glm-4.7")
    )
    if not runtime_is_pinned:
        raise ReleaseVerificationError(
            f"{workflow_name} does not pin and verify the approved OpenCode CLI runtime"
        )
    return run_step


def _verify_approved_v140_policy(tree: VerifiedCommitTree, ref: str) -> None:
    # 이 게이트는 의도적으로 v1.40 계열 두 태그에 닫힌 집합이다: 목적이 "그 두 역사적
    # 태그의 정책 파일이 승인 당시 스냅샷 그대로인지"의 사후 변조 방지이기 때문이다.
    # v1.41+ 태그의 내용 승인은 운영자가 명시적으로 넘기는 --expected-commit 핀이
    # 담당하므로 새 태그를 여기에 추가하는 것은 자동 확장이 아니라 별도의 스냅샷 승인
    # 절차다(라이브 트리를 태그로 쓰는 테스트 픽스처와도 충돌하므로 자동화하지 않는다).
    if ref not in {"v1.40", "v1.40.1"}:
        return
    digest = hashlib.sha256()
    for path in APPROVED_V140_POLICY_FILES:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(tree.read_file(path))
        digest.update(b"\0")
    if digest.hexdigest() != APPROVED_V140_POLICY_SHA256:
        raise ReleaseVerificationError(
            f"tag {ref} differs from the approved v1.40 policy snapshot"
        )


def _release_inventory(tree: VerifiedCommitTree, ref: str) -> None:
    try:
        roots = release_roots_for(ref)
        entries = validate_release_listing(tree.listing(release_paths_for(ref)), roots)
        for entry in entries:
            tree.reader.read(entry.oid, "blob")
    except (ReleaseVerificationError, ValueError):
        raise ReleaseVerificationError(
            "release inventory is incomplete or invalid"
        ) from None


def _release_version(ref: str) -> tuple[int, ...]:
    if RELEASE_REF.fullmatch(ref) is None:
        raise ReleaseVerificationError(f"invalid release ref: {ref}")
    return tuple(int(part) for part in ref.removeprefix("v").split("."))


def _verify_setup_gemini_auth(tree: VerifiedCommitTree, ref: str) -> None:
    path = SETUP_GEMINI_AUTH_ROOT.path.as_posix()
    try:
        document = yaml.load(
            tree.read_text(path), Loader=yaml.BaseLoader
        )
    except (ReleaseVerificationError, yaml.YAMLError):
        raise ReleaseVerificationError(
            "setup-gemini-auth action contract is invalid"
        ) from None
    expected = (
        EXPECTED_SETUP_GEMINI_AUTH
        if _release_version(ref) >= (1, 46)
        else EXPECTED_SETUP_GEMINI_AUTH_V145
    )
    if document != expected:
        raise ReleaseVerificationError("setup-gemini-auth action contract is invalid")


def _verify_prepare_review_diff_action(tree: VerifiedCommitTree, ref: str) -> None:
    path = PREPARE_REVIEW_DIFF_ACTION_ROOT.path.as_posix()
    try:
        document = yaml.load(tree.read_text(path), Loader=yaml.BaseLoader)
    except (ReleaseVerificationError, yaml.YAMLError):
        raise ReleaseVerificationError(
            "prepare-review-diff action contract is invalid"
        ) from None
    expected = (
        EXPECTED_PREPARE_REVIEW_DIFF_ACTION
        if _release_version(ref) >= (1, 46)
        else EXPECTED_PREPARE_REVIEW_DIFF_ACTION_V145
    )
    if document != expected:
        raise ReleaseVerificationError("prepare-review-diff action contract is invalid")
    try:
        helper = tree.read_text(
            ".github/actions/prepare-review-diff/prepare_review_diff.py"
        )
    except ReleaseVerificationError:
        raise ReleaseVerificationError(
            "prepare-review-diff immutable local Git scope contract is invalid"
        ) from None
    required = (
        "def parse_name_status(",
        "def local_scope(",
        "def git_full_diff(",
        '"--name-status"',
        '"-z"',
        '"--find-renames=50%"',
        '"--ignore-submodules=none"',
        '"--no-replace-objects"',
        "full_diff = git_full_diff(merge_base_sha, head_sha",
        "records = local_scope(merge_base_sha, head_sha",
    )
    forbidden = (
        "def pr_files(",
        "def numbered_pr_diff(",
        "/pulls/{pr_number}/files",
        '["gh", "pr", "diff"',
    )
    if (
        not all(item in helper for item in required)
        or helper.count('"--ignore-submodules=none"') != 3
        or any(item in helper for item in forbidden)
    ):
        raise ReleaseVerificationError(
            "prepare-review-diff immutable local Git scope contract is invalid"
        )


def _bound_target_names(target: ast.AST) -> tuple[str, ...]:
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, ast.Starred):
        return _bound_target_names(target.value)
    if isinstance(target, (ast.List, ast.Tuple)):
        return tuple(
            name for item in target.elts for name in _bound_target_names(item)
        )
    return ()


class _NamedExpressionBindingVisitor(ast.NodeVisitor):
    """Collect walrus targets evaluated in the surrounding module scope."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.names.extend(_bound_target_names(node.target))
        self.visit(node.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Defaults execute in the surrounding scope when the lambda is created;
        # its body executes later in the lambda's own local scope.
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)


def _expression_binding_names(node: ast.AST | None) -> tuple[str, ...]:
    if node is None:
        return ()
    visitor = _NamedExpressionBindingVisitor()
    visitor.visit(node)
    return tuple(visitor.names)


def _match_binding_names(pattern: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.append(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.append(node.rest)
    return tuple(names)


def _module_scope_binding_counts(
    module: ast.Module, protected: frozenset[str]
) -> dict[str, int]:
    """Count syntactic bindings that execute in the module namespace.

    Function and class bodies own separate namespaces. Their decorators, bases,
    defaults, and annotations are still evaluated by the surrounding module, and
    any nested ``global`` declaration for a protected name is rejected by the
    caller because it can mutate that module binding later.
    """

    counts = {name: 0 for name in protected}
    try_statement_types: tuple[type[ast.AST], ...] = (ast.Try,)
    try_star_type = getattr(ast, "TryStar", None)
    if try_star_type is not None:
        try_statement_types += (try_star_type,)
    type_alias_type = getattr(ast, "TypeAlias", None)

    def record(names: Iterable[str]) -> None:
        for name in names:
            if name in counts:
                counts[name] += 1

    def expression(node: ast.AST | None) -> None:
        record(_expression_binding_names(node))

    def function_definition(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        record((node.name,))
        for decorator in node.decorator_list:
            expression(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            expression(default)
        arguments = (
            *getattr(node.args, "posonlyargs", ()),
            *node.args.args,
            *node.args.kwonlyargs,
        )
        for argument in arguments:
            expression(argument.annotation)
        if node.args.vararg is not None:
            expression(node.args.vararg.annotation)
        if node.args.kwarg is not None:
            expression(node.args.kwarg.annotation)
        expression(node.returns)
        for type_parameter in getattr(node, "type_params", ()):
            expression(type_parameter)

    def statements(nodes: Iterable[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    record(_bound_target_names(target))
                expression(node.value)
            elif isinstance(node, ast.AnnAssign):
                record(_bound_target_names(node.target))
                expression(node.annotation)
                expression(node.value)
            elif isinstance(node, ast.AugAssign):
                record(_bound_target_names(node.target))
                expression(node.value)
            elif isinstance(node, ast.Delete):
                for target in node.targets:
                    record(_bound_target_names(target))
            elif isinstance(node, ast.Import):
                record(
                    alias.asname or alias.name.partition(".")[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" for alias in node.names):
                    record(protected)
                else:
                    record(alias.asname or alias.name for alias in node.names)
            elif type_alias_type is not None and isinstance(node, type_alias_type):
                record(_bound_target_names(node.name))
                expression(node.value)
                for type_parameter in getattr(node, "type_params", ()):
                    expression(type_parameter)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_definition(node)
            elif isinstance(node, ast.ClassDef):
                record((node.name,))
                for decorator in node.decorator_list:
                    expression(decorator)
                for base in node.bases:
                    expression(base)
                for keyword in node.keywords:
                    expression(keyword.value)
                for type_parameter in getattr(node, "type_params", ()):
                    expression(type_parameter)
            elif isinstance(node, ast.If):
                expression(node.test)
                statements(node.body)
                statements(node.orelse)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                record(_bound_target_names(node.target))
                expression(node.iter)
                statements(node.body)
                statements(node.orelse)
            elif isinstance(node, ast.While):
                expression(node.test)
                statements(node.body)
                statements(node.orelse)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    expression(item.context_expr)
                    if item.optional_vars is not None:
                        record(_bound_target_names(item.optional_vars))
                statements(node.body)
            elif isinstance(node, try_statement_types):
                statements(node.body)
                for handler in node.handlers:
                    expression(handler.type)
                    if handler.name is not None:
                        record((handler.name,))
                    statements(handler.body)
                statements(node.orelse)
                statements(node.finalbody)
            elif isinstance(node, ast.Match):
                expression(node.subject)
                for case in node.cases:
                    record(_match_binding_names(case.pattern))
                    expression(case.guard)
                    statements(case.body)
            elif isinstance(node, ast.Expr):
                expression(node.value)
            elif isinstance(node, ast.Assert):
                expression(node.test)
                expression(node.msg)
            elif isinstance(node, ast.Raise):
                expression(node.exc)
                expression(node.cause)

    statements(module.body)
    return counts


def _require_unique_module_bindings(
    module: ast.Module, protected: frozenset[str]
) -> None:
    for node in ast.walk(module):
        if isinstance(node, ast.Global) and protected.intersection(node.names):
            raise ValueError("protected symbol has a nested global binding")
    counts = _module_scope_binding_counts(module, protected)
    if any(count != 1 for count in counts.values()):
        raise ValueError("protected symbol binding is missing or duplicated")


def _module_assignment(module: ast.Module, name: str) -> ast.expr:
    matches: list[ast.expr] = []
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            matches.append(node.value)
    if len(matches) != 1:
        raise ValueError("missing or duplicate assignment")
    return matches[0]


def _static_literal(module: ast.Module, name: str) -> object:
    value = _module_assignment(module, name)
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "frozenset"
        and len(value.args) == 1
        and not value.keywords
    ):
        return frozenset(ast.literal_eval(value.args[0]))
    return ast.literal_eval(value)


def _class_node(module: ast.Module, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    if len(matches) != 1:
        raise ValueError("missing or duplicate class")
    return matches[0]


def _record_fields(node: ast.ClassDef) -> tuple[tuple[str, str], ...]:
    if [ast.unparse(item) for item in node.decorator_list] != [
        "dataclass(frozen=True)"
    ]:
        raise ValueError("record is not frozen")
    fields: list[tuple[str, str]] = []
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            if item.value is not None:
                raise ValueError("record field has a default")
            fields.append((item.target.id, ast.unparse(item.annotation)))
    return tuple(fields)


def _function_header(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    header, separator, _body = ast.unparse(node).partition(":\n")
    if separator != ":\n":
        raise ValueError("function cannot be rendered")
    return header


def _module_function_headers(module: ast.Module) -> dict[str, str]:
    functions: dict[str, str] = {}
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in functions:
                raise ValueError("duplicate function")
            functions[node.name] = _function_header(node)
    return functions


def _class_function_headers(node: ast.ClassDef) -> dict[str, str]:
    functions: dict[str, str] = {}
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if item.name in functions:
                raise ValueError("duplicate method")
            functions[item.name] = _function_header(item)
    return functions


def _verify_canonicalize_review_helpers(tree: VerifiedCommitTree) -> None:
    canonical_path = CANONICALIZE_REVIEW_HELPER_ROOT.path.as_posix()
    scope_path = REVIEW_SCOPE_HELPER_ROOT.path.as_posix()
    try:
        canonical_source = tree.read_file(canonical_path)
        scope_source = tree.read_file(scope_path)
        if (
            hashlib.sha256(canonical_source).hexdigest()
            != EXPECTED_CANONICALIZE_REVIEW_HELPER_SHA256
            or hashlib.sha256(scope_source).hexdigest()
            != EXPECTED_REVIEW_SCOPE_HELPER_SHA256
        ):
            raise ValueError("helper source digest differs")
        canonical_text = canonical_source.decode("utf-8")
        scope_text = scope_source.decode("utf-8")
        # Compilation is a syntax gate only.  Never import or execute release code.
        compile(canonical_text, canonical_path, "exec", dont_inherit=True)
        compile(scope_text, scope_path, "exec", dont_inherit=True)
        canonical_module = ast.parse(canonical_text, filename=canonical_path)
        scope_module = ast.parse(scope_text, filename=scope_path)

        canonical_literals = {
            "HARD_REASONS": EXPECTED_CANONICALIZER_HARD_REASONS,
            "SOFT_REASONS": EXPECTED_CANONICALIZER_SOFT_REASONS,
            "SEVERITIES": ("CRITICAL", "HIGH", "MEDIUM"),
            "IMPACT_CLASSES": frozenset(
                {
                    "runtime",
                    "security",
                    "data-integrity",
                    "user-visible",
                    "performance",
                }
            ),
            "MAX_CANDIDATE_BYTES": 60_000,
            "MAX_CANONICAL_BYTES": 64_000,
            "MAX_PREVIOUS_CANONICAL_BYTES": 65_536,
            "MAX_CANDIDATE_BLOCKS": 512,
        }
        _require_unique_module_bindings(
            canonical_module,
            frozenset(
                {
                    *canonical_literals,
                    "MAX_SAFE_INTEGER",
                    *EXPECTED_CANONICALIZER_RECORDS,
                    *EXPECTED_CANONICALIZER_FUNCTIONS,
                }
            ),
        )
        _require_unique_module_bindings(
            scope_module,
            frozenset(
                {
                    "GIT_ENV",
                    "ScopeValidationError",
                    *EXPECTED_SCOPE_RECORDS,
                    *EXPECTED_SCOPE_FUNCTIONS,
                }
            ),
        )
        if any(
            _static_literal(canonical_module, name) != expected
            for name, expected in canonical_literals.items()
        ):
            raise ValueError("canonical constants differ")
        expected_safe_integer = ast.parse(
            "(1 << 53) - 1", mode="eval"
        ).body
        if ast.dump(_module_assignment(canonical_module, "MAX_SAFE_INTEGER")) != ast.dump(
            expected_safe_integer
        ):
            raise ValueError("safe integer constant differs")
        if _static_literal(scope_module, "GIT_ENV") != EXPECTED_SCOPE_GIT_ENV:
            raise ValueError("scope Git environment differs")

        for name, expected in EXPECTED_CANONICALIZER_RECORDS.items():
            if _record_fields(_class_node(canonical_module, name)) != expected:
                raise ValueError("canonical record differs")
        for name, expected in EXPECTED_SCOPE_RECORDS.items():
            if _record_fields(_class_node(scope_module, name)) != expected:
                raise ValueError("scope record differs")

        scope_error = _class_node(scope_module, "ScopeValidationError")
        if [ast.unparse(base) for base in scope_error.bases] != ["ValueError"]:
            raise ValueError("scope error base differs")
        canonical_functions = _module_function_headers(canonical_module)
        scope_functions = _module_function_headers(scope_module)
        if any(
            canonical_functions.get(name) != expected
            for name, expected in EXPECTED_CANONICALIZER_FUNCTIONS.items()
        ) or any(
            scope_functions.get(name) != expected
            for name, expected in EXPECTED_SCOPE_FUNCTIONS.items()
        ):
            raise ValueError("public helper function differs")
        review_scope_methods = _class_function_headers(
            _class_node(scope_module, "ReviewScope")
        )
        if any(
            review_scope_methods.get(name) != expected
            for name, expected in EXPECTED_REVIEW_SCOPE_METHODS.items()
        ):
            raise ValueError("public scope method differs")
    except (
        ReleaseVerificationError,
        SyntaxError,
        TypeError,
        ValueError,
    ):
        raise ReleaseVerificationError(
            "canonicalize-review helper contract is invalid"
        ) from None


def _verify_canonicalize_review_action(tree: VerifiedCommitTree) -> None:
    expected_files = {
        (root.path.as_posix(), "100644", "blob")
        for root in (
            CANONICALIZE_REVIEW_ACTION_ROOT,
            CANONICALIZE_REVIEW_HELPER_ROOT,
            REVIEW_SCOPE_HELPER_ROOT,
        )
    }
    actual_files = {
        (entry.path.as_posix(), entry.mode, entry.object_type)
        for entry in tree.files(CANONICALIZE_REVIEW_ACTION_ROOT.path.parent)
    }
    if actual_files != expected_files:
        raise ReleaseVerificationError(
            "canonicalize-review inventory is not closed"
        )
    path = CANONICALIZE_REVIEW_ACTION_ROOT.path.as_posix()
    try:
        document = yaml.load(tree.read_text(path), Loader=yaml.BaseLoader)
    except (ReleaseVerificationError, yaml.YAMLError):
        raise ReleaseVerificationError(
            "canonicalize-review action contract is invalid"
        ) from None
    if document != EXPECTED_CANONICALIZE_REVIEW_ACTION:
        raise ReleaseVerificationError(
            "canonicalize-review action contract is invalid"
        )
    _verify_canonicalize_review_helpers(tree)


def _verify_claude_code_action_pin(
    ref: str, documents: dict[str, dict]
) -> None:
    if _release_version(ref) < (1, 45, 3):
        return
    expected_by_workflow = {
        "claude-code-review.yml": [
            ("claude-review", "Run Claude Code Review", CLAUDE_CODE_ACTION)
        ],
        "claude.yml": [
            ("claude", "Run Claude Code", CLAUDE_CODE_ACTION)
        ],
    }
    for filename, expected in expected_by_workflow.items():
        workflow = documents.get(filename, {})
        references = []
        for job_name, job in workflow.get("jobs", {}).items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if isinstance(uses, str) and uses.lower().startswith(
                    "anthropics/claude-code-action@"
                ):
                    references.append((job_name, step.get("name"), uses))
        if references != expected:
            raise ReleaseVerificationError(
                "Claude Code action is not the approved immutable commit"
            )


def _verify_tag_catalog(
    tree: VerifiedCommitTree, ref: str
) -> WorkflowCatalog | None:
    """Run the catalog/config/canonical contracts against tag-owned bytes."""

    import tempfile

    paths = (
        "scripts/workflow-catalog.json",
        "scripts/workflow-config.json",
        "examples/baseline-workflows/.github",
    )
    missing_inventory = {
        path
        for path in paths[:2]
        if (entry := tree.entry(path)) is None or entry.object_type != "blob"
    }
    if missing_inventory:
        # Historical tags predate the closed release inventory.  Keep their
        # OpenCode verifier regression path readable, while all v1.40+ tags
        # must carry the renderer-owned catalog and fleet config.
        if _release_version(ref) < (1, 40):
            return None
        raise ReleaseVerificationError(
            f"tag {ref} release inventory is missing: {sorted(missing_inventory)}"
        )
    with tempfile.TemporaryDirectory(prefix="verify-workflow-tag-") as temporary:
        root = Path(temporary)
        for relative in paths[:2]:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tree.read_file(relative))
        try:
            catalog = load_catalog(root)
            config = load_fleet_config(root, catalog)
        except CatalogError as exc:
            raise ReleaseVerificationError(
                f"tag {ref} catalog/config is invalid: {exc}"
            ) from exc

        if ref == "v1.40" and (
            config.owner != "jhw7500"
            or config.automation_ref != "v1.40"
            or config.canonical_dir.as_posix()
            != "examples/baseline-workflows/.github"
            or len(config.profiles) != 19
            or len(catalog.callers) != 14
        ):
            raise ReleaseVerificationError(
                "tag v1.40 violates the approved v1.40 semantic identity"
            )

        canonical_names = {entry.path.as_posix() for entry in tree.files(paths[2])}
        expected_names = {
            f"{config.canonical_dir}/{entry.path.relative_to('.github')}"
            for entry in catalog.entries
            if entry.kind != "retired"
        }
        if canonical_names != expected_names:
            missing = sorted(expected_names - canonical_names)
            unknown = sorted(canonical_names - expected_names)
            raise ReleaseVerificationError(
                f"tag {ref} canonical tree mismatch: missing={missing}, unknown={unknown}"
            )
        for entry in catalog.callers:
            relative = entry.path.relative_to(".github")
            name = f"{config.canonical_dir}/{relative}"
            text = tree.read_text(name)
            try:
                document = yaml.load(text, Loader=yaml.BaseLoader)
            except yaml.YAMLError as exc:
                raise ReleaseVerificationError(f"{name} is invalid YAML") from exc
            if not isinstance(document, dict):
                raise ReleaseVerificationError(f"{name} must be a mapping")
            if document.get("on") != entry.trigger:
                raise ReleaseVerificationError(f"{name} trigger violates the catalog")
            try:
                jobs = extract_caller_jobs(document)
            except CatalogError as exc:
                raise ReleaseVerificationError(
                    f"{name} caller contract is invalid: {exc}"
                ) from exc
            if jobs != entry.caller_jobs:
                raise ReleaseVerificationError(f"{name} caller jobs violate the catalog")
            uses = re.findall(
                r"jhw7500/automation/\.github/workflows/([^@\s'\"]+)@"
                r"([^\s'\"]+)",
                text,
            )
            if uses != [(entry.central_workflow, "__AUTOMATION_COMMIT__")]:
                raise ReleaseVerificationError(
                    f"{name} central target or release placeholder violates the catalog"
                )

        config_name = f"{config.canonical_dir}/workflow-config.yml"
        try:
            canonical_config = yaml.load(
                tree.read_text(config_name),
                Loader=yaml.BaseLoader,
            )
        except yaml.YAMLError as exc:
            raise ReleaseVerificationError(f"{config_name} is invalid YAML") from exc
        expected_workflows = {entry.path.stem for entry in catalog.callers}
        workflow_values = (
            canonical_config.get("workflows", {})
            if isinstance(canonical_config, dict)
            else {}
        )
        config_is_closed = (
            isinstance(canonical_config, dict)
            and set(canonical_config)
            == {"automation_ref", "automation_commit", "review", "workflows"}
            and canonical_config.get("automation_ref") == "__AUTOMATION_REF__"
            and canonical_config.get("automation_commit") == "__AUTOMATION_COMMIT__"
            and canonical_config.get("review") == {"auto": "false"}
            and isinstance(workflow_values, dict)
            and set(workflow_values) == expected_workflows
            and all(value == {"enabled": "false"} for value in workflow_values.values())
        )
        if not config_is_closed:
            raise ReleaseVerificationError(
                f"{config_name} violates the disabled bootstrap contract"
            )
        return catalog


def _expected_manual_fetch_step(
    contract: ManualGeminiContract,
) -> dict[str, object]:
    step_name = contract.step_name
    step_id = contract.step_id
    number_name = contract.number_name
    number_expression = contract.number_expression
    command = contract.view_command
    number_reference = f"${number_name}"
    run = (
        f'title="$({command} "{number_reference}" --repo "$REPO" '
        '--json title --jq .title)"\n'
        f'body="$({command} "{number_reference}" --repo "$REPO" '
        '--json body --jq .body)"\n\n'
        "write_output() {\n"
        '  local name="$1"\n'
        '  local value="$2"\n'
        "  local delimiter='__AUTOMATION_OUTPUT__'\n"
        '  while [[ "$value" == *"$delimiter"* ]]; do\n'
        '    delimiter="${delimiter}_X"\n'
        "  done\n"
        "  {\n"
        "    printf '%s<<%s\\n' \"$name\" \"$delimiter\"\n"
        "    printf '%s\\n' \"$value\"\n"
        "    printf '%s\\n' \"$delimiter\"\n"
        '  } >> "$GITHUB_OUTPUT"\n'
        "}\n\n"
        'write_output title "$title"\n'
        'write_output body "$body"\n'
    )
    return {
        "name": step_name,
        "id": step_id,
        "shell": "bash",
        "env": {
            "GH_TOKEN": "${{ github.token }}",
            "REPO": "${{ github.repository }}",
            number_name: number_expression,
        },
        "run": run,
    }


def _expected_manual_prepare_job(
    contract: ManualGeminiContract,
) -> dict[str, object]:
    step_id = contract.step_id
    number_name = contract.number_name
    number_expression = contract.number_expression
    permission_name = contract.permission_name
    output_prefix = contract.output_prefix
    input_name = number_name.lower()
    number_reference = f"${number_name}"
    validation = {
        "name": f"Validate {input_name}",
        "shell": "bash",
        "env": {number_name: number_expression},
        "run": (
            f'if ! [[ "{number_reference}" =~ ^[0-9]+$ ]]; then\n'
            f'  echo "{input_name} must be a positive integer"\n'
            "  exit 1\n"
            "fi\n"
        ),
    }
    return {
        "runs-on": "ubuntu-latest",
        "permissions": {permission_name: "read"},
        "outputs": {
            f"{output_prefix}_title": (
                f"${{{{ steps.{step_id}.outputs.title }}}}"
            ),
            f"{output_prefix}_body": f"${{{{ steps.{step_id}.outputs.body }}}}",
        },
        "steps": [validation, _expected_manual_fetch_step(contract)],
    }


def _verify_manual_gemini_output_contract(
    tree: VerifiedCommitTree, ref: str
) -> None:
    if _release_version(ref) < (1, 40, 2):
        return
    root = "examples/baseline-workflows/.github/workflows"
    for filename, contract in MANUAL_GEMINI_FETCH_CONTRACTS.items():
        path = f"{root}/{filename}"
        try:
            document = yaml.load(tree.read_text(path), Loader=yaml.BaseLoader)
            prepare = document["jobs"]["prepare"]
            downstream = document["jobs"][contract.downstream_job]
            downstream_with = downstream["with"]
        except (ReleaseVerificationError, yaml.YAMLError, KeyError, TypeError):
            raise ReleaseVerificationError(
                f"{path} manual Gemini output contract is invalid"
            ) from None
        if prepare != _expected_manual_prepare_job(contract):
            # 셀프서비스 진단: 무엇이 승인된 형태와 다른지 보여준다 — 이 지점은 플릿
            # 릴리즈 전체를 막는 게이트라, 단서 없는 실패는 곧바로 릴리즈 중단 비용이 된다.
            expected_text = json.dumps(
                _expected_manual_prepare_job(contract), indent=1, sort_keys=True
            )
            actual_text = json.dumps(prepare, indent=1, sort_keys=True, default=str)
            delta = "\n".join(
                difflib.unified_diff(
                    expected_text.splitlines(),
                    actual_text.splitlines(),
                    "approved prepare job",
                    "tagged prepare job",
                    lineterm="",
                    n=2,
                )
            )
            raise ReleaseVerificationError(
                f"{path} manual Gemini output contract is invalid; "
                f"prepare job differs from the approved shape:\n{delta[:4000]}"
            )
        output_prefix = contract.output_prefix
        expected_downstream = {
            "issue_title": (
                f"${{{{ needs.prepare.outputs.{output_prefix}_title }}}}"
            ),
            "issue_body": f"${{{{ needs.prepare.outputs.{output_prefix}_body }}}}",
        }
        if (
            downstream.get("needs") != "prepare"
            or not isinstance(downstream_with, dict)
            or any(
                downstream_with.get(name) != value
                for name, value in expected_downstream.items()
            )
        ):
            raise ReleaseVerificationError(
                f"{path} manual Gemini output contract is invalid"
            )


def _values(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            result.append(str(key))
            result.extend(_values(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_values(item))
    elif isinstance(value, str):
        result.append(value)
    return result


def _resolver_candidate(step: dict) -> bool:
    uses = step.get("uses", "")
    return (
        step.get("id") == "auth"
        or step.get("name") == "Resolve repository-write token"
        or (isinstance(uses, str) and "setup-gemini-auth" in uses)
    )


def _grants_write(permissions: object) -> bool:
    return permissions == "write-all" or (
        isinstance(permissions, dict)
        and any(value == "write" for value in permissions.values())
    )


def _action_references(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        uses = value.get("uses")
        if isinstance(uses, str):
            result.append(uses)
        for item in value.values():
            result.extend(_action_references(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_action_references(item))
    return result


def expected_review_actions(ref: str, workflow: str) -> list[str]:
    """Return the exact ordered release-local action dependencies."""

    actions: list[str] = []
    if _release_version(ref) >= (1, 46) and workflow == "gemini-auto-review.yml":
        actions.append(SETUP_GEMINI_AUTH_REVIEW)
    if release_supports_prepare_review_diff(ref):
        actions.append(PREPARE_REVIEW_DIFF_ACTION)
    if (
        release_supports_canonicalize_review(ref)
        and workflow in CANONICALIZE_REVIEW_WORKFLOWS
    ):
        actions.append(CANONICALIZE_REVIEW_ACTION)
    return actions


def _verify_review_action_dependencies(
    ref: str, documents: dict[str, dict]
) -> None:
    for name in REVIEW_DIFF_DEPENDENCY_WORKFLOWS:
        expected = expected_review_actions(ref, name)
        document = documents.get(name)
        if document is None:
            if expected:
                raise ReleaseVerificationError(
                    f"{name} prepare-review-diff dependency or review action "
                    "dependency contract is missing"
                )
            continue
        references = _action_references(document)
        local_references = [
            reference
            for reference in references
            if reference.startswith(
                ("$/.github/actions/", "./.github/actions/")
            )
        ]
        if local_references != expected:
            raise ReleaseVerificationError(
                f"{name} prepare-review-diff dependency or review action "
                "dependency contract is invalid"
            )
        if _release_version(ref) >= (1, 46) and name == "opencode-auto-review.yml":
            prepare_steps = [
                step
                for job in document.get("jobs", {}).values()
                if isinstance(job, dict)
                for step in job.get("steps", [])
                if isinstance(step, dict)
                and step.get("uses") == PREPARE_REVIEW_DIFF_ACTION
            ]
            if len(prepare_steps) != 1 or prepare_steps[0].get("with", {}).get(
                "output-directory"
            ) != "${{ github.workspace }}":
                raise ReleaseVerificationError(
                    f"{name} prepare-review-diff output directory contract is invalid"
                )


def _named_step(job: object, name: str) -> dict:
    if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
        raise ValueError("review job has no steps")
    matches = [
        step
        for step in job["steps"]
        if isinstance(step, dict) and step.get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError("review step is missing or duplicated")
    return matches[0]


def _expected_canonicalize_step(contract: dict[str, str]) -> dict[str, object]:
    reviewer = contract["reviewer"]
    reset = contract["reset"]
    return {
        "name": contract["canonical_step"],
        "id": "canonicalize-review",
        "if": (
            f"${{{{ always() && steps.{reset}.outcome == 'success' && "
            "steps.prepare-diff.outputs.diff-ready == 'true' && "
            "steps.prepare-diff.outputs.diff-mode != 'unchanged' }}"
        ),
        "uses": CANONICALIZE_REVIEW_ACTION,
        "with": {
            "reviewer": reviewer,
            "candidate-file": f"${{{{ github.workspace }}}}/{contract['raw']}",
            "canonical-file": (
                f"${{{{ github.workspace }}}}/{contract['canonical']}"
            ),
            "result-file": (
                f"${{{{ github.workspace }}}}/{reviewer}-review-result.json"
            ),
            "scope-manifest": "${{ runner.temp }}/review-scope.json",
            "selected-diff": (
                "${{ steps.prepare-diff.outputs.diff-mode == 'delta' && "
                "format('{0}/review-delta.diff', runner.temp) || "
                "format('{0}/review-full.diff', runner.temp) }}"
            ),
            "diff-mode": "${{ steps.prepare-diff.outputs.diff-mode }}",
            "previous-sha": contract["previous_sha"],
            "previous-review-file": contract["previous_file"],
        },
    }


def _expected_prepared_head_checkout_step() -> dict[str, object]:
    return {
        "name": "Checkout prepared review head",
        "if": "${{ steps.prepare-diff.outputs.diff-ready == 'true' }}",
        "uses": CHECKOUT_ACTION,
        "with": {
            "ref": "${{ steps.prepare-diff.outputs.head-sha }}",
            "fetch-depth": "0",
            "clean": "false",
            "persist-credentials": "false",
        },
    }


def _expected_rejected_review_diagnostic_upload_step(
    contract: dict[str, str],
) -> dict[str, object]:
    reviewer = contract["reviewer"]
    return {
        "name": f"Upload rejected {reviewer.capitalize()} review diagnostic",
        "if": (
            "${{ always() && steps.canonicalize-review.outcome != 'skipped' "
            "&& steps.canonicalize-review.outputs.document-valid != 'true' }}"
        ),
        "uses": UPLOAD_ARTIFACT_ACTION,
        "with": {
            "name": (
                f"{reviewer}-review-diagnostic-${{{{ github.run_id }}}}-"
                "${{ github.run_attempt }}"
            ),
            "path": f"${{{{ github.workspace }}}}/{reviewer}-review-result.json",
            "if-no-files-found": "ignore",
            "retention-days": "1",
            "overwrite": "false",
        },
    }


def _verify_review_publication_contracts(documents: dict[str, dict]) -> None:
    jq_state_keys = json.dumps(list(QUALITY_STATE_KEYS))
    js_state_keys = ", ".join(repr(key) for key in QUALITY_STATE_KEYS)
    expected_output_env = {
        "CANONICAL_OUTCOME": "${{ steps.canonicalize-review.outcome }}",
        "DOCUMENT_VALID": "${{ steps.canonicalize-review.outputs.document-valid }}",
        "ACCEPTED_COUNT": "${{ steps.canonicalize-review.outputs.accepted-count }}",
        "FILTERED_COUNT": "${{ steps.canonicalize-review.outputs.filtered-count }}",
        "NORMALIZED_COUNT": "${{ steps.canonicalize-review.outputs.normalized-count }}",
        "FILTERED_MAX_SEVERITY": (
            "${{ steps.canonicalize-review.outputs.filtered-max-severity }}"
        ),
        "CANONICAL_FAILURE_REASON": (
            "${{ steps.canonicalize-review.outputs.failure-reason }}"
        ),
    }
    prompt_rules = (
        "Changed anchor:",
        "Trigger evidence:",
        "Material impact:",
        "Performance basis:",
        "RVW-<12hex>",
        "Resolved requires a code change; Retracted requires evidence",
    )
    try:
        for name, raw_contract in REVIEW_PUBLICATION_CONTRACTS.items():
            contract = {
                key: value
                for key, value in raw_contract.items()
                if isinstance(value, str)
            }
            document = documents[name]
            job = document["jobs"][contract["job"]]
            if job.get("permissions", {}).get("actions") != "read":
                raise ValueError("review run provenance permission differs")
            canonical_step = _named_step(job, contract["canonical_step"])
            if canonical_step != _expected_canonicalize_step(contract):
                raise ValueError("canonicalizer call differs")
            rejected_diagnostic_upload = _named_step(
                job,
                f"Upload rejected {contract['reviewer'].capitalize()} review diagnostic",
            )
            if (
                rejected_diagnostic_upload
                != _expected_rejected_review_diagnostic_upload_step(contract)
            ):
                raise ValueError("rejected review diagnostic differs")
            prepared_head_checkout = _named_step(job, "Checkout prepared review head")
            if prepared_head_checkout != _expected_prepared_head_checkout_step():
                raise ValueError("prepared review head checkout differs")

            collector = _named_step(job, contract["collector"])
            collector_script = collector.get("run", "")
            if not isinstance(collector_script, str):
                raise ValueError("collector script is unavailable")
            collector_env = collector.get("env", {})
            marker = contract["marker"]
            collector_marker = (
                collector.get("env", {}).get("MARKER") == marker
                if contract["reviewer"] == "claude"
                else f"MARKER='{marker}'" in collector_script
            )
            collector_token_contract = contract["reviewer"] == "claude" or (
                isinstance(collector_env, dict)
                and collector_env.get("ACTIONS_TOKEN") == "${{ github.token }}"
                and 'GH_TOKEN="$ACTIONS_TOKEN" gh api' in collector_script
            )
            collector_publisher_contract = (
                contract["reviewer"] == "claude"
                and 'select(.user.type == "Bot" and .user.login == $bot_login)'
                in collector_script
            ) or (
                contract["reviewer"] == "gemini"
                and collector_env.get("AUTH_MODE") == contract["auth_mode"]
                and collector_env.get("PUBLISHER_APP_ID")
                == contract["publisher_app_id"]
                and "def publisher_matches(" in collector_script
                and "performed_via_github_app" in collector_script
                and 'app_publisher("15368")' in collector_script
                and "app_publisher($publisher_app_id)" in collector_script
                and "select(publisher_matches(" in collector_script
            )
            collector_contract = (
                collector_marker
                and collector_token_contract
                and collector_publisher_contract
                and collector.get("id") == contract["collector_id"]
                and f"== {jq_state_keys}" in collector_script
                and "$s.schema == 3" in collector_script
                and "$s.quality_schema == 1" in collector_script
                and "canonical_body" in collector_script
                and "@base64" in collector_script
                and 'base64 --decode > "$PREVIOUS_FILE"' in collector_script
                and "- Validation: accepted=" in collector_script
                and isinstance(collector_env, dict)
                and collector_env.get("BOT_LOGIN") == contract["bot_login"]
                and '"${RUNNER_TEMP:?}/' in collector_script
                and '"$GITHUB_WORKSPACE/' not in collector_script
                and "sort_by(.state.run_id, .state.run_attempt, .comment.id)"
                in collector_script
                and "reverse | .[:20]" in collector_script
                and "gh api --include" in collector_script
                and "lookup_status\" = '404'" in collector_script
                and '"$lookup_exit" -ne 0' in collector_script
                and '"$lookup_status" != \'200\'' in collector_script
                and "Prior review provenance lookup is uncertain" in collector_script
                and "Failed to fetch the prior review comment snapshot" in collector_script
                and "proceeding without re-review context" not in collector_script
                and ("actions/runs/${candidate_run_id}/attempts/${candidate_attempt}")
                in collector_script
                and '.event == "pull_request" and .head_sha == $head'
                in collector_script
                and ".repository.full_name == $repo" in collector_script
                and ".number == $pr" in collector_script
                and ".head.sha == $head" not in collector_script
                and ".referenced_workflows[]?" in collector_script
                and contract["workflow_prefix"] in collector_script
            )
            if not collector_contract:
                raise ValueError("collector state differs")

            provider = _named_step(job, contract["provider"])
            prepare = _named_step(job, "Prepare review diff")
            if prepare.get("with", {}).get("output-directory") != "${{ runner.temp }}":
                raise ValueError("review diff output directory differs")
            if not (
                job["steps"].index(prepare)
                < job["steps"].index(prepared_head_checkout)
                < job["steps"].index(provider)
                < job["steps"].index(canonical_step)
                < job["steps"].index(rejected_diagnostic_upload)
            ):
                raise ValueError("prepared review head checkout order differs")
            prompt = (
                provider.get("with", {}).get("prompt", "")
                if contract["prompt_location"] == "with"
                else provider.get("run", "")
            )
            if not isinstance(prompt, str) or any(
                prompt.count(rule) < 1 for rule in prompt_rules
            ):
                raise ValueError("quality prompt differs")

            upsert = _named_step(job, "Upsert review comment")
            if upsert.get("if") != contract["upsert_if"]:
                raise ValueError("upsert prior-state guard differs")
            if not (
                job["steps"].index(rejected_diagnostic_upload)
                < job["steps"].index(upsert)
            ):
                raise ValueError("rejected review diagnostic order differs")
            upsert_script = upsert.get("with", {}).get("script", "")
            if not isinstance(upsert_script, str):
                raise ValueError("upsert script is unavailable")
            if (
                hashlib.sha256(upsert_script.encode("utf-8")).hexdigest()
                != contract["upsert_sha256"]
            ):
                raise ValueError("upsert publication program differs")
            validation_template = (
                "`- Validation: accepted=${state.accepted_count}; "
                "filtered=${state.filtered_count}; "
                "normalized=${state.normalized_count}; "
                "filtered_max=${state.filtered_max_severity}`"
            )
            upsert_run_lookup_contract = (
                contract["reviewer"] == "claude"
                and "github.rest.actions.getWorkflowRunAttempt" in upsert_script
            ) or (
                contract["reviewer"] == "gemini"
                and "github.request.endpoint" in upsert_script
                and "authorization: `Bearer ${actionsToken}`" in upsert_script
                and "const response = await fetch(request.url" in upsert_script
                and "signal: AbortSignal.timeout(30000)" in upsert_script
            )
            upsert_publisher_contract = (
                contract["reviewer"] == "claude"
                and "comment.user?.login !== botLogin" in upsert_script
            ) or (
                contract["reviewer"] == "gemini"
                and "const publisherMatches = (comment)" in upsert_script
                and "performed_via_github_app" in upsert_script
                and "appPublisherMatches(comment, '15368')" in upsert_script
                and "appPublisherMatches(comment, publisherAppId)" in upsert_script
                and "if (!publisherMatches(comment)) return null;" in upsert_script
                and "if (!publisherMatches(comment)) return false;" in upsert_script
            )
            upsert_contract = (
                f"const marker = '{marker}';" in upsert_script
                and f"const v2Marker = '{contract['v2_marker']}';" in upsert_script
                and ":v1 -->" not in upsert_script
                and f"const expectedStateKeys = [{js_state_keys}];" in upsert_script
                and "state.schema === 3" in upsert_script
                and "state.quality_schema === 1" in upsert_script
                and "schema: 3" in upsert_script
                and "quality_schema: 1" in upsert_script
                and validation_template in upsert_script
                and "const v2Target = existing ? null : exactDisplayTarget(v2Marker);"
                in upsert_script
                and (
                    f"fs.readFileSync('{contract['canonical']}', 'utf8')"
                    in upsert_script
                )
                and contract["raw"] not in upsert_script
                and upsert_publisher_contract
                and upsert_run_lookup_contract
                and contract["workflow_prefix"] in upsert_script
                and "run?.event === 'pull_request'" in upsert_script
                and "run?.head_sha === record.state.attempt_head" in upsert_script
                and "run?.repository?.full_name === repository" in upsert_script
                and "pr?.number === issueNumber" in upsert_script
                and "pr?.head?.sha" not in upsert_script
                and "run?.referenced_workflows" in upsert_script
                and "const successQuality = unchangedInputIsValid ? {" in upsert_script
                and all(
                    f"preserveSuccess ? existing.state.{field} : null"
                    in upsert_script
                    for field in (
                        "accepted_count",
                        "filtered_count",
                        "normalized_count",
                        "filtered_max_severity",
                    )
                )
            )
            if not upsert_contract:
                raise ValueError("upsert publication differs")
            upsert_env = upsert.get("env", {})
            upsert_token_contract = contract["reviewer"] == "claude" or (
                isinstance(upsert_env, dict)
                and upsert_env.get("ACTIONS_TOKEN") == "${{ github.token }}"
            )
            upsert_publisher_env_contract = contract["reviewer"] == "claude" or (
                upsert_env.get("AUTH_MODE") == contract["auth_mode"]
                and upsert_env.get("PUBLISHER_APP_ID")
                == contract["publisher_app_id"]
            )
            if (
                not isinstance(upsert_env, dict)
                or any(
                    upsert_env.get(key) != value
                    for key, value in expected_output_env.items()
                )
                or upsert_env.get("BOT_LOGIN") != contract["bot_login"]
                or not upsert_token_contract
                or not upsert_publisher_env_contract
            ):
                raise ValueError("canonicalizer output bridge differs")
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ReleaseVerificationError(
            "review publication contract is invalid"
        ) from None


def _verify_token_mapping(
    name: str,
    location: str,
    mapping: object,
    *,
    allow_empty: bool = False,
    allow_actions_provenance: bool = False,
) -> int:
    if not isinstance(mapping, dict):
        return 0
    sinks = 0
    for key, value in mapping.items():
        token_key = str(key).lower().replace("_", "-")
        if token_key == "token" or token_key.endswith("-token"):
            if token_key == "actions-token" and allow_actions_provenance:
                if value != GITHUB_ACTIONS_PROVENANCE_TOKEN:
                    raise ReleaseVerificationError(
                        f"{name}:{location} Actions provenance token is invalid"
                    )
                continue
            if allow_empty:
                if value in {None, ""}:
                    continue
                raise ReleaseVerificationError(
                    f"{name}:{location} sets a repository token before resolver"
                )
            sinks += 1
            if value != GEMINI_AUTH_OUTPUT:
                raise ReleaseVerificationError(
                    f"{name}:{location} repository token sink must use steps.auth"
                )
    return sinks


def _verify_gemini_workflow(name: str, document: dict, ref: str) -> None:
    try:
        call = document["on"]["workflow_call"]
        inputs = call["inputs"]
        secrets = call["secrets"]
    except (KeyError, TypeError) as exc:
        raise ReleaseVerificationError(
            f"{name} Gemini workflow_call contract is missing"
        ) from exc
    mode = inputs.get("repo_write_auth") if isinstance(inputs, dict) else None
    supports_publisher_migration = (
        _release_version(ref) >= (1, 46) and name == "gemini-auto-review.yml"
    )
    publisher_input = (
        inputs.get("publisher_app_id") if isinstance(inputs, dict) else None
    )
    if (
        set(document.get("on", {})) != {"workflow_call"}
        or mode != EXPECTED_GEMINI_MODE_INPUT
        or inputs.get("app_id") != EXPECTED_GEMINI_APP_ID_INPUT
        or (
            supports_publisher_migration
            and publisher_input != EXPECTED_GEMINI_PUBLISHER_APP_ID_INPUT
        )
        or (not supports_publisher_migration and publisher_input is not None)
    ):
        raise ReleaseVerificationError(
            f"{name} must declare exact repo_write_auth, app_id, and "
            "publisher_app_id inputs"
        )
    values = _values(document)
    normalized = "\n".join(values)
    normalized_lower = normalized.lower()
    if "google_api_key" in normalized_lower:
        raise ReleaseVerificationError(f"{name} contains forbidden GOOGLE_API_KEY")
    if secrets != EXPECTED_GEMINI_SECRETS:
        raise ReleaseVerificationError(
            f"{name} must declare only APP_PRIVATE_KEY and GEMINI_API_KEY"
        )
    if any(
        forbidden in normalized_lower
        for forbidden in (
            "google-github-actions/auth",
            "google_application_credentials",
            "workload_identity_provider",
            "gcp_",
            "id-token",
        )
    ):
        raise ReleaseVerificationError(f"{name} contains forbidden Google/GCP/OIDC auth")
    if "actions/create-github-app-token" in normalized_lower:
        raise ReleaseVerificationError(
            f"{name} contains forbidden direct App token resolver"
        )
    if "vars.app_id" in normalized_lower:
        raise ReleaseVerificationError(f"{name} contains forbidden ambient App fallback")
    if _grants_write(document.get("permissions")):
        raise ReleaseVerificationError(
            f"{name} must not grant workflow-level write permissions"
        )
    expected_setup_action = (
        SETUP_GEMINI_AUTH_REVIEW
        if _release_version(ref) >= (1, 46) and name == "gemini-auto-review.yml"
        else SETUP_GEMINI_AUTH
    )
    approved_actions = APPROVED_GEMINI_ACTIONS | {expected_setup_action}
    if release_supports_prepare_review_diff(ref):
        approved_actions |= {PREPARE_REVIEW_DIFF_ACTION}
    if release_supports_canonicalize_review(ref):
        approved_actions |= {
            CANONICALIZE_REVIEW_ACTION,
            UPLOAD_ARTIFACT_ACTION,
        }
    unapproved_actions = sorted(set(_action_references(document)) - approved_actions)
    if unapproved_actions:
        raise ReleaseVerificationError(
            f"{name} uses a resolver/action outside the approved action allowlist: "
            f"{unapproved_actions[0]}"
        )
    workflow_metadata = {key: value for key, value in document.items() if key != "jobs"}
    if "github.token" in "\n".join(_values(workflow_metadata)):
        raise ReleaseVerificationError(
            f"{name}:workflow contains forbidden github.token outside resolver"
        )
    _verify_token_mapping(name, "workflow.env", document.get("env", {}), allow_empty=True)

    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict):
        raise ReleaseVerificationError(f"{name} jobs must be a mapping")
    resolver_count = 0
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            raise ReleaseVerificationError(f"{name}:{job_name} job must be a mapping")
        steps = job.get("steps", [])
        if not isinstance(steps, list) or not all(
            isinstance(step, dict) for step in steps
        ):
            raise ReleaseVerificationError(f"{name}:{job_name} steps are invalid")
        if "permissions" not in job or not isinstance(job["permissions"], dict):
            raise ReleaseVerificationError(
                f"{name}:{job_name} must declare an explicit permissions mapping"
            )
        candidates = [
            index for index, step in enumerate(steps) if _resolver_candidate(step)
        ]
        permissions = job["permissions"]
        writes = _grants_write(permissions)
        if writes and len(candidates) != 1:
            raise ReleaseVerificationError(
                f"{name}:{job_name} must contain exactly one setup-gemini-auth resolver"
            )
        if not writes and candidates:
            raise ReleaseVerificationError(
                f"{name}:{job_name} contains a resolver outside a write job"
            )

        metadata = {key: value for key, value in job.items() if key != "steps"}
        if "github.token" in "\n".join(_values(metadata)):
            raise ReleaseVerificationError(
                f"{name}:{job_name} contains forbidden github.token outside resolver"
            )
        for step_index, step in enumerate(steps):
            step_values = "\n".join(_values(step))
            step_env = step.get("env", {})
            provenance_step = (
                _release_version(ref) >= (1, 46)
                and name == "gemini-auto-review.yml"
                and job_name == "gemini-review"
                and step.get("name") in {"Get PR details", "Upsert review comment"}
                and isinstance(step_env, dict)
                and step_env.get("ACTIONS_TOKEN")
                == GITHUB_ACTIONS_PROVENANCE_TOKEN
                and step_values.count("github.token") == 1
            )
            if (
                step_index not in candidates
                and "github.token" in step_values
                and not provenance_step
            ):
                raise ReleaseVerificationError(
                    f"{name}:{job_name} bypasses the resolved repository-write token"
                )

        if not candidates:
            continue
        resolver_count += 1
        index = candidates[0]
        resolver = steps[index]
        if resolver != {
            "name": "Resolve repository-write token",
            "id": "auth",
            "uses": expected_setup_action,
            "with": EXPECTED_GEMINI_AUTH_WITH,
        }:
            raise ReleaseVerificationError(
                f"{name}:{job_name} setup-gemini-auth resolver must use the exact "
                "release-bound action and mode-controlled inputs"
            )
        expected_validation = (
            EXPECTED_GEMINI_AUTO_VALIDATION
            if supports_publisher_migration
            else EXPECTED_GEMINI_VALIDATION
        )
        if index == 0 or steps[index - 1] != expected_validation:
            raise ReleaseVerificationError(
                f"{name}:{job_name} resolver must be immediately preceded by exact "
                "repository-write auth validation"
            )

        write_token_sinks = _verify_token_mapping(
            name, f"{job_name}.env", job.get("env", {}), allow_empty=True
        )
        for step_index, step in enumerate(steps):
            if step_index == index:
                continue
            provenance_step = (
                _release_version(ref) >= (1, 46)
                and name == "gemini-auto-review.yml"
                and job_name == "gemini-review"
                and step.get("name") in {"Get PR details", "Upsert review comment"}
            )
            for mapping_name in ("env", "with"):
                write_token_sinks += _verify_token_mapping(
                    name,
                    f"{job_name}.steps[{step_index}].{mapping_name}",
                    step.get(mapping_name, {}),
                    allow_empty=step_index < index,
                    allow_actions_provenance=(
                        provenance_step and mapping_name == "env"
                    ),
                )
        if write_token_sinks == 0:
            raise ReleaseVerificationError(
                f"{name}:{job_name} has no resolved repository-write token sink"
            )
    if resolver_count == 0:
        raise ReleaseVerificationError(f"{name} has no setup-gemini-auth resolver")


def _verify_commit_content(
    repo: Path, ref: str, revision: str
) -> VerifiedCommitTree:
    tree = VerifiedCommitTree.open(repo, revision)
    if _release_version(ref) >= (1, 40):
        _release_inventory(tree, ref)
        _verify_setup_gemini_auth(tree, ref)
    if release_supports_prepare_review_diff(ref):
        _verify_prepare_review_diff_action(tree, ref)
    if release_supports_canonicalize_review(ref):
        _verify_canonicalize_review_action(tree)
    _verify_approved_v140_policy(tree, ref)
    _verify_manual_gemini_output_contract(tree, ref)
    catalog = _verify_tag_catalog(tree, ref)
    names = [entry.path.as_posix() for entry in tree.files(".github/workflows")]
    workflows = [name for name in names if name.endswith((".yml", ".yaml"))]
    if not workflows:
        raise ReleaseVerificationError(f"tag {ref} contains no reusable workflows")

    central_workflows = set(REVIEW_DIFF_DEPENDENCY_WORKFLOWS)
    if catalog is not None:
        central_workflows.update(
            entry.central_workflow
            for entry in catalog.callers
            if entry.central_workflow is not None
        )
    workflow_root = PurePosixPath(".github/workflows")
    documents: dict[str, dict] = {}
    for name in workflows:
        text = tree.read_text(name)
        if "secrets.GITHUB_TOKEN" in text:
            raise ReleaseVerificationError(f"{name} uses secrets.GITHUB_TOKEN")
        for match in re.finditer(r"actions/checkout@([^'\"\s#]+)", text):
            checkout = f"actions/checkout@{match.group(1)}"
            if checkout != CHECKOUT_ACTION:
                raise ReleaseVerificationError(
                    f"{name} checkout reference is not the approved immutable commit"
                )
        data = yaml.load(text, Loader=yaml.BaseLoader)
        relative = PurePosixPath(name).relative_to(workflow_root)
        if len(relative.parts) != 1:
            if relative.name in central_workflows:
                raise ReleaseVerificationError(
                    f"{name} is an unexpected nested central review workflow"
                )
            continue
        documents[relative.name] = data if isinstance(data, dict) else {}

    _verify_claude_code_action_pin(ref, documents)
    _verify_review_action_dependencies(ref, documents)
    if release_supports_canonicalize_review(ref):
        _verify_review_publication_contracts(documents)

    if catalog is not None:
        gemini_targets = sorted(
            {
                entry.central_workflow
                for entry in catalog.callers
                if entry.auth_family == "gemini"
            }
        )
        for target in gemini_targets:
            if target is None or target not in documents:
                raise ReleaseVerificationError(
                    f"central Gemini workflow is missing: {target}"
                )
            _verify_gemini_workflow(target, documents[target], ref)

        for entry in catalog.callers:
            assert entry.central_workflow is not None
            central = documents.get(entry.central_workflow)
            if central is None:
                raise ReleaseVerificationError(
                    f"central workflow is missing: {entry.central_workflow}"
                )
            try:
                call = central["on"]["workflow_call"]
                declared_inputs = set(call.get("inputs", {}))
                declared_secrets = call.get("secrets", {})
            except (KeyError, TypeError) as exc:
                raise ReleaseVerificationError(
                    f"{entry.central_workflow} workflow_call contract is missing"
                ) from exc
            if not isinstance(declared_secrets, dict):
                raise ReleaseVerificationError(
                    f"{entry.central_workflow} secrets must be a mapping"
                )
            required_secrets = {
                name
                for name, value in declared_secrets.items()
                if isinstance(value, dict) and value.get("required", "false") == "true"
            }
            if any(
                not set(job.with_keys) <= declared_inputs
                or not set(job.secrets) <= set(declared_secrets)
                or not required_secrets <= set(job.secrets)
                for job in entry.caller_jobs
            ):
                raise ReleaseVerificationError(
                    f"{entry.path} is incompatible with {entry.central_workflow}"
                )

    auto = documents.get("opencode-auto-review.yml")
    if auto is None:
        raise ReleaseVerificationError("opencode-auto-review.yml is missing")
    try:
        check_job = auto["jobs"]["check-enabled"]
        job = auto["jobs"]["opencode-review"]
    except (KeyError, TypeError) as exc:
        raise ReleaseVerificationError("OpenCode security structure is missing") from exc
    modern_auto_review = release_supports_prepare_review_diff(ref)
    approved_legacy_opencode_release = (
        ref,
        revision,
    ) in APPROVED_LEGACY_GENERIC_OPENCODE_RELEASES
    step = verify_opencode_runtime(
        job,
        "Run OpenCode PR review",
        "opencode-auto-review.yml",
        generic_run=modern_auto_review,
        allow_legacy_generic=approved_legacy_opencode_release,
        workflow_sha256=hashlib.sha256(
            tree.read_file(".github/workflows/opencode-auto-review.yml")
        ).hexdigest(),
    )
    expected_permissions: dict[str, str] = (
        {}
        if modern_auto_review
        else {"contents": "read", "pull-requests": "write", "issues": "write"}
    )
    if job.get("permissions") != expected_permissions:
        raise ReleaseVerificationError(
            f"OpenCode auto review permissions differ from {expected_permissions}"
        )
    if modern_auto_review:
        try:
            prepare = auto["jobs"]["opencode-prepare"]
            canonical = auto["jobs"]["opencode-canonicalize"]
            prepare_collect = next(item for item in prepare["steps"] if item.get("name") == "Collect previous review context")
            upload = next(item for item in prepare["steps"] if item.get("name") == "Upload sealed canonicalization handoff")
            model_download = next(item for item in job["steps"] if item.get("name") == "Download sealed review handoff")
            model_validate = next(item for item in job["steps"] if item.get("name") == "Validate sealed review handoff")
            candidate_upload = next(item for item in job["steps"] if item.get("name") == "Upload untrusted OpenCode candidate")
            canonical_download = next(item for item in canonical["steps"] if item.get("name") == "Download sealed canonicalization handoff")
            candidate_download = next(item for item in canonical["steps"] if item.get("name") == "Download untrusted OpenCode candidate")
            canonical_step = next(item for item in canonical["steps"] if item.get("name") == "Canonicalize OpenCode review")
            canonical_checkout = next(item for item in canonical["steps"] if item.get("name") == "Checkout trusted repository")
        except (KeyError, TypeError, StopIteration) as exc:
            raise ReleaseVerificationError("OpenCode three-job attestation boundary is missing") from exc
        expected_prepare = {"actions": "read", "checks": "read", "contents": "read", "pull-requests": "read", "issues": "read"}
        expected_canonical = {"actions": "read", "checks": "write", "contents": "read", "pull-requests": "write", "issues": "write"}
        canonical_script = canonical_step.get("with", {}).get("script", "")
        prepare_script = prepare_collect.get("run", "")
        model_validation = model_validate.get("run", "")
        anchor_range = (
            "`${manifest.merge_base_sha}..${manifest.head_sha}`, '--', "
            "...pathspecs,"
        )
        canonical_anchor_contract = (
            "const parseNameStatus = (bytes) => {" in canonical_script
            and "new TextDecoder('utf-8', { fatal: true })" in canonical_script
            and "bytes.at(-1) !== 0" in canonical_script
            and "? [file.previous_filename, file.filename] : [file.filename]"
            in canonical_script
            and canonical_script.count(
                "'--no-replace-objects', '--literal-pathspecs', '-c', "
                "'diff.external=',"
            )
            == (2 if approved_legacy_opencode_release else 5)
            and (
                "'diff', '--no-ext-diff', '--no-textconv', '--name-status', "
                "'-z',\n      '--find-renames=50%', "
                "'--ignore-submodules=none',"
            )
            in canonical_script
            and (
                "'diff', '--no-ext-diff', '--no-textconv', "
                "'--find-renames=50%',\n      "
                "'--ignore-submodules=none', '--inter-hunk-context=0', "
                "'--no-color', '-U0',"
            )
            in canonical_script
            and canonical_script.count(anchor_range) == 2
            and "records.length !== 1 || records[0].status !== file.status"
            in canonical_script
            and "records[0].filename !== file.filename" in canonical_script
            and (
                "(records[0].previous_filename || null) !== "
                "(file.previous_filename || null)"
            )
            in canonical_script
            and "const parseAddedRanges = (patch) => {" in canonical_script
            and "if (!patch.endsWith('\\n')) return null;" in canonical_script
            and "} else if (line.startsWith('+')) {" in canonical_script
            and "addLine(newLine, line.slice(1));" in canonical_script
            and "ranges.addedLines = addedLines;" in canonical_script
            and "typeof location.currentLine !== 'string'" in canonical_script
            and "ranges.addedLines.get(anchor.line) === anchor.currentLine"
            in canonical_script
            and canonical_script.count(
                "if (inHunk && (oldRemaining !== 0 || newRemaining !== 0)) "
                "return null;"
            )
            == 2
            and "if (!inHunk || lastBodyPrefix === null" in canonical_script
            and "(lastBodyPrefix === '+' && newRemaining !== 0)"
            in canonical_script
            and "(lastBodyPrefix === '-' && oldRemaining !== 0)"
            in canonical_script
            and (
                "lastBodyPrefix === ' '\n            "
                "&& (oldRemaining !== 0 || newRemaining !== 0)"
            )
            in canonical_script
            and "const oldEnd = oldStart + oldCount;" in canonical_script
            and "const newEnd = newStart + newCount;" in canonical_script
            and (
                "[oldStart, oldCount, newStart, newCount, oldEnd, newEnd]\n"
                "          .every(Number.isSafeInteger)"
            )
            in canonical_script
            and (
                "previousOldEnd !== null && (oldStart < previousOldEnd\n"
                "            || newStart < previousNewEnd || "
                "oldStart === previousOldStart\n"
                "            || newStart === previousNewStart)"
            )
            in canonical_script
            and "(oldCount === 0 && newCount === 0)" in canonical_script
            and "let oldEofMarked = false;" in canonical_script
            and "let newEofMarked = false;" in canonical_script
            and (
                "if (inHunk && (oldEofMarked || newEofMarked)) return null;"
            )
            in canonical_script
            and (
                "if (lastBodyPrefix === '+' || lastBodyPrefix === ' ') "
                "newEofMarked = true;"
            )
            in canonical_script
            and (
                "if (lastBodyPrefix === '-' || lastBodyPrefix === ' ') "
                "oldEofMarked = true;"
            )
            in canonical_script
            and "const ranges = parseAddedRanges(result.stdout);" in canonical_script
            and "const start = Number(match[1]);" not in canonical_script
        )
        canonical_removed_contract = (
            "const EVIDENCE_FIELD = /" in canonical_script
            and "const EVIDENCE_FIELD_NAME = /" in canonical_script
            and "const MARKDOWN_EVIDENCE_LABEL = /" in canonical_script
            and "const HTML_NUMERIC_EVIDENCE_ENTITY = /&#(?:(?:[xX]"
            in canonical_script
            and "const HTML_NAMED_EVIDENCE_ENTITY = /" in canonical_script
            and "const NAMED_EVIDENCE_REPLACEMENTS = new Map(["
            in canonical_script
            and "const HTML_EVIDENCE_COMMENT = /" in canonical_script
            and "const HTML_EVIDENCE_TAG = /" in canonical_script
            and "const normalizeEvidenceFieldWrappers = (line) => {" in canonical_script
            and "HTML_NUMERIC_EVIDENCE_ENTITY, (raw, hex, decimal) => {"
            in canonical_script
            and ").replace(HTML_NAMED_EVIDENCE_ENTITY, (raw, name) =>"
            in canonical_script
            and "NAMED_EVIDENCE_REPLACEMENTS.get(name) ?? raw"
            in canonical_script
            and "normalized = normalized.replace(/\\p{Cf}/gu, ' ');"
            in canonical_script
            and "const consumeBalanced = (value, start, opening, closing) => {"
            in canonical_script
            and "let quote = null;" in canonical_script
            and "if (quote !== null) {" in canonical_script
            and "const hasMarkdownEvidenceField = (line) => {" in canonical_script
            and canonical_script.count(
                "while (cursor < line.length "
                "&& EVIDENCE_FIELD_PADDING.test(line[cursor]))"
            )
            == 2
            and "replace(HTML_EVIDENCE_COMMENT, '')" in canonical_script
            and "normalized.replace(HTML_EVIDENCE_TAG, '')" in canonical_script
            and "EVIDENCE_FIELD.test(normalizedEvidenceLine)" in canonical_script
            and "hasMarkdownEvidenceField(normalizedEvidenceLine)"
            in canonical_script
            and (
                "if (section.name !== 'Resolved' || !removedPair || "
                "!noCurrentPair) return null;"
            )
            in canonical_script
            and (
                "evidence.removedLines[0] !== previous[0].currentLines[0]"
            )
            in canonical_script
            and "const removedLines = new Map();" in canonical_script
            and "removedLines.set(oldLine, line.slice(1));" in canonical_script
            and "ranges.removedLines = removedLines;" in canonical_script
            and (
                "'merge-base', '--is-ancestor', previousHead, attemptHead"
            )
            in canonical_script
            and "const previousNameStatus = spawnSync('/usr/bin/git'" in canonical_script
            and "identityRecords.length !== 1" in canonical_script
            and (
                "!['changed', 'modified', 'removed'].includes("
                "identityRecords[0].status)"
            )
            in canonical_script
            and (
                "ranges.removedLines.get(removal.line) !== removal.currentLine"
            )
            in canonical_script
            and "const previousContentDiff = spawnSync('/usr/bin/git'"
            in canonical_script
            and (
                "'diff', '--no-ext-diff', '--no-textconv', '--text', "
                "'--find-renames=50%',"
            )
            in canonical_script
            and (
                "'--output-indicator-new=%', "
                "`${previousHead}..${attemptHead}`"
            )
            in canonical_script
            and "const globallyAddedLines = new Set(" in canonical_script
            and "globallyAddedLines.has(removal.currentLine)" in canonical_script
        )
        body_limit_gate = canonical_script.find(
            "if (Buffer.byteLength(bodyFor(Number.MAX_SAFE_INTEGER), 'utf8') "
            "> 65536)"
        )
        repair_call = canonical_script.find("if (!(await repairComments())) return;")
        canonical_pre_mutation_size_contract = (
            body_limit_gate >= 0
            and repair_call > body_limit_gate
            and canonical_script.count("if (!(await repairComments())) return;") == 1
        )
        artifact_contract = (
            prepare.get("permissions") == expected_prepare
            and canonical.get("permissions") == expected_canonical
            and job.get("permissions") == {}
            and not any("actions/checkout@" in item.get("uses", "") for item in job.get("steps", []))
            and prepare.get("needs") == "check-enabled"
            and job.get("needs") == ["check-enabled", "opencode-prepare"]
            and canonical.get("needs") == ["check-enabled", "opencode-prepare", "opencode-review"]
            and upload.get("uses") == UPLOAD_ARTIFACT_ACTION
            and upload.get("with", {}).get("overwrite") == "false"
            and candidate_upload.get("uses") == UPLOAD_ARTIFACT_ACTION
            and candidate_upload.get("with", {}).get("name") == "opencode-candidate-${{ github.run_id }}-${{ github.run_attempt }}"
            and candidate_upload.get("with", {}).get("path") == "${{ runner.temp }}/opencode-candidate/review.md"
            and candidate_upload.get("with", {}).get("overwrite") == "false"
            and all(item.get("uses") == DOWNLOAD_ARTIFACT_ACTION for item in (model_download, canonical_download, candidate_download))
            and all(item.get("with", {}).get("artifact-ids") == "${{ needs.opencode-prepare.outputs.handoff_artifact_id }}" for item in (model_download, canonical_download))
            and all(item.get("with", {}).get("merge-multiple") == "true" for item in (model_download, canonical_download))
            and candidate_download.get("with", {}).get("artifact-ids") == "${{ needs.opencode-review.outputs.candidate_artifact_id }}"
            and candidate_download.get("with", {}).get("merge-multiple") == "true"
            and job.get("outputs", {}).get("candidate_artifact_id") == "${{ steps.upload-candidate.outputs.artifact-id }}"
            and job.get("outputs", {}).get("candidate_artifact_digest") == "${{ steps.upload-candidate.outputs.artifact-digest }}"
            and model_validate.get("env", {}).get("HANDOFF_ARTIFACT_DIGEST") == "${{ needs.opencode-prepare.outputs.handoff_artifact_digest }}"
            and '[[ "$HANDOFF_ARTIFACT_DIGEST" =~ ^[0-9a-f]{64}$ ]]' in model_validation
            and canonical_step.get("env", {}).get("HANDOFF_ARTIFACT_DIGEST") == "${{ needs.opencode-prepare.outputs.handoff_artifact_digest }}"
            and 'artifact.digest !== `sha256:${process.env.HANDOFF_ARTIFACT_DIGEST}`' in canonical_script
            and 'candidateArtifact.digest !== `sha256:${candidateDigest}`' in canonical_script
            and 'candidateArtifact.name !== candidateName' in canonical_script
            and 'candidateArtifact.workflow_run?.id !== runId' in canonical_script
            and "entries.length !== 1 || entries[0].name !== 'review.md'" in canonical_script
            and "candidateStat.size > 60000" in canonical_script
            and "Buffer.byteLength(bodyFor(Number.MAX_SAFE_INTEGER), 'utf8') > 65536" in canonical_script
            and (
                "match[1] !== JSON.stringify({ path: anchor.path, line: anchor.line })"
                if approved_legacy_opencode_release
                else "raw !== JSON.stringify({ path: anchor.path, line: anchor.line })"
            )
            in canonical_script
            and canonical_anchor_contract
            and (canonical_removed_contract or approved_legacy_opencode_release)
            and canonical_pre_mutation_size_contract
            and "github.rest.checks.create" in canonical_script
            and "github.rest.checks.update" in canonical_script
            and "github.rest.actions.listWorkflowRunsForRepo" in canonical_script
            and "github.rest.checks.listForRef" in canonical_script
            and "response.data.workflow_runs.filter((run) =>" in canonical_script
            and ").length === 1).slice(0, 20)" in canonical_script
            and "check_run_id: record.attestationId" not in canonical_script
            and "event: 'pull_request', per_page: 100, page: 1" in canonical_script
            and "event: 'pull_request', status: 'success'" not in canonical_script
            and "check_name: 'automation/opencode-canonical-review'" in canonical_script
            and "name: 'automation/opencode-canonical-review', head_sha: workflowHead" in canonical_script
            and "workflow_head: workflowHead" in canonical_script
            and "prepared_run_attempt: handoff.run_attempt" in canonical_script
            and "github.rest.actions.getWorkflowRunAttempt" in canonical_script
            and canonical_script.count("run_id: a.run_id, attempt_number: a.run_attempt") == 2
            and "a.run_attempt <= selectedRun.run_attempt" in canonical_script
            and "const claimed = comments.map(parseRecord).filter(Boolean);" in canonical_script
            and "if (bounded.length > 40)" in canonical_script
            and "for (const candidate of bounded)" in canonical_script
            and "bounded.slice(0, 40)" not in canonical_script
            and "if (run?.status !== 'completed')" in canonical_script
            and "const unresolvedAttemptEvidence = new Map();" in canonical_script
            and canonical_script.count("unresolvedAttemptEvidence.set(cacheKey, candidate);") == 2
            and canonical_script.count("unresolvedAttemptEvidence.delete(cacheKey);") == 2
            and "unresolvedAttemptEvidence.clear()" not in canonical_script
            and "const seenAttemptEvidence = new Set();" in canonical_script
            and "if (seenAttemptEvidence.size > 40)" in canonical_script
            and "unresolvedAttemptEvidence.size > 0" in canonical_script
            and "Deferring OpenCode repair while exact attempt provenance is pending" in canonical_script
            and "handoff.run_attempt > runAttempt" in canonical_script
            and "const maxUntrustedCleanupComments = 20;" in canonical_script
            and "for (const raw of commentCandidates)" not in canonical_script
            and "for (const raw of neutralizedCommentCandidates)" in canonical_script
            and 'gh api "repos/${GITHUB_REPOSITORY}/actions/runs" --method GET' in prepare_script
            and '-f event=pull_request -F per_page=100 -F page=1' in prepare_script
            and '-f status=success' not in prepare_script
            and 'commits/${workflow_head}/check-runs' in prepare_script
            and 'check-runs/${check_id}' not in prepare_script
            and 'a.run_attempt <= run.run_attempt' in prepare_script
            and "if (unique.length > 40)" in prepare_script
            and "JSON.stringify(unique)" in prepare_script
            and "unique.slice(0, 40)" not in prepare_script
            and prepare.get("outputs", {}).get("workflow_head_sha") == "${{ steps.build-handoff.outputs.workflow_head_sha }}"
            and canonical_step.get("env", {}).get("WORKFLOW_HEAD") == "${{ needs.opencode-prepare.outputs.workflow_head_sha }}"
            and "automation-attestation" in canonical_script
            and canonical_checkout.get("with", {}).get("persist-credentials") == "false"
            and canonical_checkout.get("with", {}).get("ref") == "${{ needs.opencode-prepare.outputs.head_sha }}"
            and "always()" in canonical.get("if", "")
        )
        if not artifact_contract:
            raise ReleaseVerificationError("OpenCode sealed handoff/attestation contract is invalid")
        expected_caller_permissions = {
            "actions": "read", "checks": "write", "contents": "read",
            "issues": "write", "pull-requests": "write",
        }
        opencode_entry = next(
            (entry for entry in catalog.callers
             if entry.path.as_posix() == ".github/workflows/opencode-auto-review.yml"),
            None,
        ) if catalog is not None else None
        self_document = documents.get("_self-opencode-auto-review.yml", {})
        self_job = self_document.get("jobs", {}).get("opencode-review", {})
        if (
            opencode_entry is None
            or len(opencode_entry.caller_jobs) != 1
            or dict(opencode_entry.caller_jobs[0].permissions) != expected_caller_permissions
            or self_job.get("permissions") != expected_caller_permissions
            or self_job.get("uses") != "./.github/workflows/opencode-auto-review.yml"
        ):
            raise ReleaseVerificationError("OpenCode caller permission ceiling is invalid")
    safe_output = check_job.get("outputs", {}).get("safe_pr")
    scope_step = next(
        (item for item in check_job.get("steps", []) if item.get("id") == "pr_scope"),
        {},
    )
    condition = job.get("if", "")
    if (
        safe_output != "${{ steps.pr_scope.outputs.safe_pr }}"
        or "gh api" not in scope_step.get("run", "")
        or not isinstance(condition, str)
        or "needs.check-enabled.outputs.safe_pr == 'true'" not in condition
    ):
        raise ReleaseVerificationError(
            "OpenCode auto review lacks a central same-repository PR guard"
        )
    if modern_auto_review:
        if {"GITHUB_TOKEN", "GH_TOKEN", "USE_GITHUB_TOKEN"} & set(step.get("env", {})):
            raise ReleaseVerificationError("OpenCode auto review model receives a GitHub token")
        if any(
            "actions/checkout@" in item.get("uses", "")
            for item in job.get("steps", [])
        ):
            raise ReleaseVerificationError("OpenCode auto review model receives a repository checkout")
    else:
        try:
            checkout = next(
                item
                for item in job["steps"]
                if item.get("name") == "Checkout repository"
            )
        except (KeyError, TypeError, StopIteration) as exc:
            raise ReleaseVerificationError(
                "OpenCode auto review checkout is missing"
            ) from exc
        if (
            checkout.get("with", {}).get("persist-credentials") != "true"
            or step.get("env", {}).get("GITHUB_TOKEN") != "${{ github.token }}"
        ):
            raise ReleaseVerificationError(
                "OpenCode auto review cannot authenticate its historical private repository fetch"
            )

    command = documents.get("opencode.yml")
    try:
        command_check = command["jobs"]["check-enabled"]
        command_job = command["jobs"]["opencode"]
        command_checkout = next(
            item
            for item in command_job["steps"]
            if item.get("name") == "Checkout repository"
        )
    except (KeyError, TypeError, StopIteration) as exc:
        raise ReleaseVerificationError("opencode.yml structure is missing") from exc
    command_step = verify_opencode_runtime(command_job, "Run opencode", "opencode.yml")
    if command_checkout.get("with", {}).get("persist-credentials") != "true":
        raise ReleaseVerificationError(
            "opencode.yml cannot authenticate private repository fetch"
        )
    command_scope = next(
        (item for item in command_check.get("steps", []) if item.get("id") == "pr_scope"),
        {},
    )
    command_condition = command_job.get("if", "")
    command_permissions = {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    command_is_secure = (
        command_check.get("outputs", {}).get("safe_pr")
        == "${{ steps.pr_scope.outputs.safe_pr }}"
        and "gh api" in command_scope.get("run", "")
        and command_scope.get("env", {}).get("PR_NUMBER")
        == "${{ github.event.pull_request.number || github.event.issue.number }}"
        and isinstance(command_condition, str)
        and "needs.check-enabled.outputs.safe_pr == 'true'" in command_condition
        and command_job.get("permissions") == command_permissions
        and command_step.get("env", {}).get("USE_GITHUB_TOKEN") == "true"
        and command_step.get("env", {}).get("GITHUB_TOKEN") == "${{ github.token }}"
    )
    if not command_is_secure:
        raise ReleaseVerificationError(
            "opencode.yml security contract permits unsafe PR or App-token access"
        )
    return tree


def verify_commit_content(repo: Path, ref: str, commit: str) -> str:
    """Verify one exact raw commit before an immutable release tag is published."""

    if RELEASE_REF.fullmatch(ref) is None:
        raise ReleaseVerificationError(f"invalid release ref: {ref}")
    revision = resolve_commit(repo, commit)
    _verify_commit_content(repo, ref, revision)
    return revision


def verify_tag_content(
    repo: Path, ref: str, *, tag: AnnotatedTag | None = None
) -> VerifiedCommitTree:
    captured = tag if tag is not None else resolve_annotated_tag(repo, ref)
    if captured.ref != ref:
        raise ReleaseVerificationError("captured tag identity does not match release ref")
    tree = _verify_commit_content(repo, ref, captured.commit)
    assert_tag_unchanged(repo, captured)
    return tree


def verify_release(
    repo: Path, ref: str, expected_commit: str, remote: str | None = None
) -> str:
    if re.fullmatch(r"v[0-9]+(?:\.[0-9]+)+", ref) is None:
        raise ReleaseVerificationError(f"invalid release ref: {ref}")
    tag = resolve_annotated_tag(repo, ref)
    expected = resolve_commit(repo, expected_commit)
    if tag.commit != expected:
        raise ReleaseVerificationError(
            f"tag {ref} points to {tag.commit}, expected commit {expected}"
        )
    verify_tag_content(repo, ref, tag=tag)
    if remote is not None:
        verify_remote_tag(repo, remote, tag)
    assert_tag_unchanged(repo, tag)
    return tag.commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--remote")
    parser.add_argument("--commit-only", action="store_true")
    args = parser.parse_args(argv)
    if args.commit_only and args.remote is not None:
        parser.error("--commit-only does not accept --remote")
    try:
        commit = (
            verify_commit_content(args.automation, args.ref, args.expected_commit)
            if args.commit_only
            else verify_release(
                args.automation, args.ref, args.expected_commit, remote=args.remote
            )
        )
    except ReleaseVerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if args.commit_only:
        print(f"PASS: {args.ref} commit content is secure at {commit}")
    else:
        remote_note = f" and remote {args.remote}" if args.remote else ""
        print(f"PASS: {args.ref} resolves to secure commit {commit}{remote_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
