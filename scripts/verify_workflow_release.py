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
import shlex
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
    REVIEW_INVOCATION_BUDGET_ACTION_ROOT,
    REVIEW_INVOCATION_BUDGET_HELPER_ROOT,
    REVIEW_POLICY_ACTION_ROOT,
    REVIEW_POLICY_HELPER_ROOT,
    REVIEW_SCOPE_HELPER_ROOT,
    SETUP_GEMINI_AUTH_ROOT,
    release_paths_for,
    release_roots_for,
    release_supports_canonicalize_review,
    release_supports_prepare_review_diff,
    release_supports_review_invocation_budget,
    release_supports_review_optin,
    release_supports_review_rounds_variable,
    release_supports_filter_reason_surface,
    release_supports_finding_dismissal,
    release_supports_label_review_trigger,
    release_supports_skip_reason_notice,
    release_supports_label_mismatch_decline,
    release_supports_dispatch_review_diff,
    release_supports_opencode_finding_ids,
    release_supports_opencode_dismissals,
    release_retires_manual_pr_review,
    release_supports_same_head_cancel_guard,
    release_supports_review_policy,
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
OPENCODE_REVIEW_RUN_V147_SHA256 = (
    "be6e7fc1c937cacc1c789463ec80a1612621395b02a5050b0923fe373fb4265d"
)
OPENCODE_AUTO_REVIEW_SHA256 = (
    "a38218bc27e672f7f7bde1873b9fa3de811057490f3fab7dc91c74d03d80ba97"
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
        "force-full": {"required": "false", "default": "false"},
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
                    "FORCE_FULL": "${{ inputs.force-full }}",
                    "CONTEXT_LINES": "${{ inputs.context-lines }}",
                    "OUTPUT_DIRECTORY": "${{ inputs.output-directory }}",
                },
                "run": (
                    "set -euo pipefail\n"
                    "force_args=()\n"
                    "if [[ \"$FORCE_FULL\" == 'true' ]]; then\n"
                    "  force_args+=(--force-full)\n"
                    "elif [[ \"$FORCE_FULL\" != 'false' ]]; then\n"
                    "  echo 'force-full must be true or false' >&2\n"
                    "  exit 2\n"
                    "fi\n"
                    'python3 "$GITHUB_ACTION_PATH/prepare_review_diff.py" \\\n'
                    '  --repository "$GITHUB_REPOSITORY" \\\n'
                    '  --pr-number "$PR_NUMBER" \\\n'
                    '  --previous-sha "$PREVIOUS_SHA" \\\n'
                    '  --previous-full-hash "$PREVIOUS_FULL_HASH" \\\n'
                    '  --context-lines "$CONTEXT_LINES" \\\n'
                    '  --full-output "$OUTPUT_DIRECTORY/review-full.diff" \\\n'
                    '  --delta-output "$OUTPUT_DIRECTORY/review-delta.diff" \\\n'
                    '  --manifest-output "$OUTPUT_DIRECTORY/review-scope.json" \\\n'
                    '  --github-output "$GITHUB_OUTPUT" \\\n'
                    '  "${force_args[@]}"\n'
                ),
            }
        ],
    },
}
# v1.45.x wrote its prepared files into GITHUB_WORKSPACE. v1.46 introduces the
# required output-directory input so the final checkout cannot replace them.
EXPECTED_PREPARE_REVIEW_DIFF_ACTION_V145 = deepcopy(EXPECTED_PREPARE_REVIEW_DIFF_ACTION)
del EXPECTED_PREPARE_REVIEW_DIFF_ACTION_V145["inputs"]["output-directory"]
del EXPECTED_PREPARE_REVIEW_DIFF_ACTION_V145["inputs"]["force-full"]
_legacy_prepare_step = EXPECTED_PREPARE_REVIEW_DIFF_ACTION_V145["runs"]["steps"][0]
del _legacy_prepare_step["env"]["OUTPUT_DIRECTORY"]
del _legacy_prepare_step["env"]["FORCE_FULL"]
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


def _canonicalize_action_with_filter_reasons(action: dict) -> dict:
    """Derive the v1.62 canonicalizer action: one added scalar output.

    Stating the delta keeps the two expectations from drifting apart.
    """

    updated = deepcopy(action)
    updated["outputs"]["filtered-reasons"] = {
        "value": "${{ steps.canonicalize.outputs.filtered_reasons }}"
    }
    return updated


EXPECTED_CANONICALIZE_REVIEW_ACTION_V162 = _canonicalize_action_with_filter_reasons(
    EXPECTED_CANONICALIZE_REVIEW_ACTION
)


def _canonicalize_action_with_dismissals(action: dict) -> dict:
    """Derive the v1.63 canonicalizer action: one optional input bridged to one flag."""

    updated = deepcopy(action)
    updated["inputs"]["dismissed-finding-ids"] = {"required": "false", "default": ""}
    step = updated["runs"]["steps"][0]
    step["env"]["DISMISSED_FINDING_IDS"] = "${{ inputs.dismissed-finding-ids }}"
    anchor = '--repository-root "$GITHUB_WORKSPACE" '
    if step["run"].count(anchor) != 1:
        raise ValueError("canonicalize action run block differs")
    step["run"] = step["run"].replace(
        anchor, '--dismissed-finding-ids "$DISMISSED_FINDING_IDS" ' + anchor
    )
    return updated


EXPECTED_CANONICALIZE_REVIEW_ACTION_V163 = _canonicalize_action_with_dismissals(
    EXPECTED_CANONICALIZE_REVIEW_ACTION_V162
)
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
# v1.63 normalizes a carryover or re-emission of a finding a collaborator dismissed.
EXPECTED_CANONICALIZER_SOFT_REASONS_V163 = EXPECTED_CANONICALIZER_SOFT_REASONS | {
    "dismissed_prior_id",
}
EXPECTED_CANONICALIZE_REVIEW_HELPER_SHA256 = (
    "eb4ba827d3b03e3c9169cd95e9194d49b2b8b9b6956e7317b5c9cc9b7bb04fc5"
)
EXPECTED_CANONICALIZE_REVIEW_HELPER_SHA256_V162 = (
    "d814b42568e38a2f46f1772d00a5a713a467bba067d8865cf4f7657a38ac0739"
)
EXPECTED_CANONICALIZE_REVIEW_HELPER_SHA256_V163 = (
    "36a2a9301e60f806c4ee32441a0bb98fccad091d96dfb63b6ca25773d3a28b3e"
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
    "CandidateValidation": (
        ("attempt", "Literal['initial']"),
        ("sha256", "str"),
        ("valid", "Literal[False]"),
        ("rule", "str"),
        ("line", "int"),
        ("column", "int"),
    ),
    "CanonicalizationResult": (
        ("document_valid", "bool"),
        ("accepted_count", "int"),
        ("filtered_count", "int"),
        ("normalized_count", "int"),
        ("filtered_max_severity", "Literal['none', 'MEDIUM', 'HIGH', 'CRITICAL']"),
        ("failure_reason", "str"),
        ("candidate_reasons", "tuple[CandidateReason, ...]"),
        ("candidate_validations", "tuple[CandidateValidation, ...]"),
    ),
}
# v1.63 hands the canonicalizer the finding IDs a collaborator dismissed.
EXPECTED_CANONICALIZER_RECORDS_V163 = {
    **EXPECTED_CANONICALIZER_RECORDS,
    "CanonicalizationRequest": EXPECTED_CANONICALIZER_RECORDS["CanonicalizationRequest"] + (
        ("dismissed_finding_ids", "frozenset[str]"),
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
REVIEW_INVOCATION_BUDGET_ACTION = (
    f"$/{REVIEW_INVOCATION_BUDGET_ACTION_ROOT.path.parent.as_posix()}"
)
REVIEW_POLICY_ACTION = (
    f"$/{REVIEW_POLICY_ACTION_ROOT.path.parent.as_posix()}"
)
REVIEWER_WORKFLOWS = {
    "claude": "claude-code-review.yml",
    "gemini": "gemini-auto-review.yml",
    "opencode": "opencode-auto-review.yml",
}
EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_SHA256 = (
    "70b50ce482ff0e54df9fff88d5126cd8e760ed8bdabfefcc2f2ccdc639cb693b"
)
EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_SHA256_V160 = (
    "d9cb26a5c340abd20707483f05f4e071b436dac17a900f670aacbd05140b981e"
)
EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_SHA256_V163 = (
    "b9ebc50e0959d9a2db82b1b11715be81309581ac45bb29b6dab02dccacedb91c"
)
EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256 = (
    "43f15d59df0f529e2fa4f06488e49dc5ff78280762ee8d7248dbecb45cbb609d"
)
EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V160 = (
    "d6a9dc0af9b6e340b6528911ac60a48e21fd8960e515167e2c6be4536f33f1a3"
)
EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V162 = (
    "deda9f65bbe853860fa06599ce303b15cd63b15b5dad645896c8c76aae7e9b03"
)
EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V163 = (
    "2123326a018589644cd9f5e1e49bdb33eaca8096272bd8396583da2f0f9518b7"
)
# v1.71 stops excluding OpenCode from dismissals: that reviewer gained `RVW-` identifiers in
# v1.70, so the guard that skipped it — and the one that hid the dismissal line from its ledger
# comment — no longer describe anything true.
EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V171 = (
    "0435025c49ec29357220ee934abc9946440c7019a6d5a47e1586632851de1cbd"
)
EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256 = {
    "claude": "d4db53b86603a3a113999409e2e4e35c397adf276981e7f68c0ea57a6198faa2",
    "gemini": "cbd69aba74ec0f3380591f14d0adcb6543faad5e237a855dbec246750e2b5206",
    "opencode": "0b6515397370851a21180b1e4c87091746057fe500a41b32ba7460fea0e63ac5",
}
EXPECTED_REVIEW_POLICY_WORKFLOW_SHA256 = {
    "claude": "8e16c59b04aeaaf7419571b0bda3fb1e46a29da3b847bbb0409ab0437a1027d7",
    "gemini": "ac48c84dbc60071e88f89d6ecadebb781b50d5beb7da0e0a1ec363fab5599ce8",
    "opencode": "08f2c4cc96d6ac6753fcde8ec9fda9778ed396b9546dd7f69273423940752b9e",
}
# v1.60 reads the automatic-round budget from REVIEW_MAX_ROUNDS, so the three
# reusable review workflows changed bytes again.
EXPECTED_REVIEW_ROUNDS_VARIABLE_WORKFLOW_SHA256 = {
    "claude": "939d8ec9dab85d670881d93681d428af4486b410112a9e9af032a473923430ea",
    "gemini": "a33c941b20dc1d9d14a221ee9360dd6cda90801c3c7dba2347971840df87b326",
    "opencode": "caaefcdc244444718302cc230d4bb868651e697abc503afab3d75d786c6d29c0",
}
# v1.61 narrowed cancel-in-progress to superseding commits, changing the three
# reusable review workflows again.
# v1.62 writes filtered-finding reasons to the run summary for Claude and Gemini;
# the OpenCode workflow is unchanged on this line.
EXPECTED_FILTER_REASON_WORKFLOW_SHA256 = {
    "claude": "03f2208f44a84e3c726903c5ed707d2b9a5ebab9cf934cf2aa19ddd8182c3f14",
    "gemini": "07bf3ace69eeeca2bf8243d52b0e35dfafcdd2689f40fe9afd211a597095d1b4",
    "opencode": "4a173036252929de6320da7e04436d67b9a4759ed4b9f60b6bdf2b3a3ecc1d5a",
}
EXPECTED_SAME_HEAD_CANCEL_WORKFLOW_SHA256 = {
    "claude": "b3b7d95cb8e87164470759f4c918f8c317e37a10d2c214ec915ac4a51963f04e",
    "gemini": "3271f4f7dd4d13711b91d65ac191451703ead424fcb67299ff504b8a26b32e0f",
    "opencode": "4a173036252929de6320da7e04436d67b9a4759ed4b9f60b6bdf2b3a3ecc1d5a",
}
# v1.63 hands the budget action's dismissed finding IDs to the shared canonicalizer in
# the Claude and Gemini workflows; OpenCode derives no finding IDs and is unchanged.
EXPECTED_FINDING_DISMISSAL_WORKFLOW_SHA256 = {
    "claude": "b32973a2434b7f3314c26d764f91715fc5f0770b5c26be6c2327212457f54ae2",
    "gemini": "3ea5ef5d0c3f05beadb4fbf6dcf605840286b603123a5dac918cec3bd92414fe",
    "opencode": "4a173036252929de6320da7e04436d67b9a4759ed4b9f60b6bdf2b3a3ecc1d5a",
}
EXPECTED_SKIP_REASON_WORKFLOW_SHA256 = {
    "claude": "4fe4bf0be84f3b155ec948d6628bf557babb6fff6bdc05ce2344d72c208a4a53",
    "gemini": "c05f48186aefe8058fd26c4084f51202cc52649a3c97f853b85bae34335e1882",
    "opencode": "4a173036252929de6320da7e04436d67b9a4759ed4b9f60b6bdf2b3a3ecc1d5a",
}
EXPECTED_LABEL_MISMATCH_WORKFLOW_SHA256 = {
    "claude": "a6116cf542876a46e8401e26471324a586772398ad5d21360155e686123104be",
    "gemini": "33c15251ac3e7dd97a3c0d30c77dd40e6ac58fe087d93078ce468dba473027b2",
    "opencode": "a74b0b0948467e46bebf223fc2b5f2bfcd3824c317a908dbacbb7e7cd2ec6fbc",
}
# v1.70 stamps a stable RVW- identifier on every published OpenCode finding so a dismissal
# comment has something to name. Claude and Gemini derive theirs in the shared canonicalizer
# and are unchanged, so they keep the digests the label-mismatch release approved.
EXPECTED_OPENCODE_FINDING_ID_WORKFLOW_SHA256 = {
    "claude": "a6116cf542876a46e8401e26471324a586772398ad5d21360155e686123104be",
    "gemini": "33c15251ac3e7dd97a3c0d30c77dd40e6ac58fe087d93078ce468dba473027b2",
    "opencode": "218292d680481a85ece7f118c0d4c38f855682c4ebe6941322d5fee2799143d0",
}
# v1.71 applies dismissals in the OpenCode canonicalizer and validates carryover anchors one
# block at a time. Claude and Gemini are unchanged.
EXPECTED_OPENCODE_DISMISSAL_WORKFLOW_SHA256 = {
    "claude": "a6116cf542876a46e8401e26471324a586772398ad5d21360155e686123104be",
    "gemini": "33c15251ac3e7dd97a3c0d30c77dd40e6ac58fe087d93078ce468dba473027b2",
    "opencode": "3803e294dc7975af1a2b6812639fd11ee55c97d6f92258281d2df770c8d6c433",
}
EXPECTED_REVIEW_POLICY_HELPER_SHA256 = (
    "3e0fd3c86b1dc40dc35213ca41c3d63122c9ebf757042f5a2c86f4fc1e99ac8a"
)
EXPECTED_REVIEW_POLICY_HELPER_SHA256_V166 = (
    "9a8d79dc6838f036ff7694dea947726f8a111086aaa967b41d27a9a55c8f84b5"
)
EXPECTED_REVIEW_POLICY_HELPER_SHA256_V159 = (
    "50a4cbaf364e326201c2572435ab4dc9dcac5e14ff4349cfd790a1b26f4092b7"
)
EXPECTED_REVIEW_POLICY_RECORDS = {
    "PolicyRequest": (
        ("workflow_name", "str"),
        ("review_mode", "str"),
        ("force_run", "bool"),
        ("force_review", "bool"),
        ("event_name", "str"),
        ("repository", "str"),
        ("pr", "dict[str, object]"),
        ("config", "dict[str, object]"),
    ),
    "PolicyDecision": (
        ("run_review", "bool"),
        ("effective_mode", "str"),
        ("reason", "str"),
        ("head_sha", "str"),
    ),
}
EXPECTED_REVIEW_POLICY_LITERALS = frozenset(
    {
        "PolicyError",
        "PolicyRequest",
        "PolicyDecision",
        "resolve_policy",
        "review:request",
        "review:skip",
        "review_label_conflict",
        "review_mode_label_mismatch",
        "workflow_auto_false",
        "review_auto_false",
        "default_auto_true",
    }
)
# v1.59 flipped the unconfigured automatic decision to opt-in; every other
# literal is unchanged, so the delta is stated rather than duplicated.
EXPECTED_REVIEW_POLICY_LITERALS_V159 = (
    EXPECTED_REVIEW_POLICY_LITERALS - {"default_auto_true"}
) | {"default_auto_false"}
EXPECTED_REVIEW_POLICY_ACTION = yaml.load(
    r"""name: Resolve Review Policy
description: Resolve a deterministic review policy before model invocation.

inputs:
  workflow-name:
    description: Workflow name in workflow-config.yml.
    required: true
  pr-number:
    description: Pull request number.
    required: true
  review-mode:
    description: Requested review mode.
    required: true
  force-run:
    description: Run even when the workflow is disabled.
    required: true
  force-review:
    description: Request a review for a manual dispatch.
    required: true
  github-token:
    description: Token used to read the pull request.
    required: true

outputs:
  run-review:
    description: Whether a review may run.
    value: ${{ steps.resolve.outputs.run-review }}
  effective-mode:
    description: The resolved review mode.
    value: ${{ steps.resolve.outputs.effective-mode }}
  reason:
    description: Deterministic resolution reason.
    value: ${{ steps.resolve.outputs.reason }}
  head-sha:
    description: Validated pull request head SHA.
    value: ${{ steps.resolve.outputs.head-sha }}

runs:
  using: composite
  steps:
    - id: resolve
      shell: bash
      env:
        GH_TOKEN: ${{ inputs.github-token }}
        PR_NUMBER: ${{ inputs.pr-number }}
        REVIEW_MODE: ${{ inputs.review-mode }}
        WORKFLOW_NAME: ${{ inputs.workflow-name }}
        FORCE_RUN: ${{ inputs.force-run }}
        FORCE_REVIEW: ${{ inputs.force-review }}
      run: |
        set -euo pipefail
        policy_dir="$RUNNER_TEMP/review-policy-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
        install -d -m 0700 "$policy_dir"
        gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" > "$policy_dir/pr.json"
        ruby -ryaml -rjson -e 'cfg = File.file?(ARGV[0]) ? (YAML.safe_load_file(ARGV[0], aliases: false) || {}) : {}; File.write(ARGV[1], JSON.generate(cfg))' \
          .github/workflow-config.yml "$policy_dir/config.json"
        python3 - "$policy_dir/pr.json" "$policy_dir/config.json" "$policy_dir/request.json" <<'PY'
        import json
        import os
        import sys
        from pathlib import Path

        def boolean(name: str) -> bool:
            value = os.environ[name]
            if value not in {"true", "false"}:
                raise SystemExit(f"{name.lower()}_invalid")
            return value == "true"

        pr_path, config_path, request_path = map(Path, sys.argv[1:])
        payload = {
            "workflow_name": os.environ["WORKFLOW_NAME"],
            "review_mode": os.environ["REVIEW_MODE"],
            "force_run": boolean("FORCE_RUN"),
            "force_review": boolean("FORCE_REVIEW"),
            "event_name": os.environ["GITHUB_EVENT_NAME"],
            "repository": os.environ["GITHUB_REPOSITORY"],
            "pr": json.loads(pr_path.read_text(encoding="utf-8")),
            "config": json.loads(config_path.read_text(encoding="utf-8")),
        }
        request_path.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        PY
        python3 "$GITHUB_ACTION_PATH/resolve_review_policy.py" \
          --request-file "$policy_dir/request.json" \
          --result-file "$policy_dir/result.json" \
          --github-output "$GITHUB_OUTPUT"
""",
    Loader=yaml.BaseLoader,
)
EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION = yaml.load(
    r"""name: Review invocation budget
description: Claim and finalize a durable fail-closed review invocation budget
inputs:
  github-token:
    required: true
  mode:
    required: true
  reviewer:
    required: true
  pr-number:
    required: true
  expected-head-sha:
    required: true
  full-diff-sha256:
    required: true
  diff-mode:
    required: true
  force-review:
    required: false
    default: 'false'
  input-files-json:
    required: true
  authenticated-review-json:
    required: true
  model-route-json:
    required: true
  effort:
    required: true
  checkpoint-file:
    required: true
  actual-call-count:
    required: false
    default: '0'
  elapsed-seconds:
    required: false
    default: '0'
  outcome:
    required: false
    default: checkpoint_failure
  stop-reason:
    required: false
    default: ''
  remaining-finding-ids-json:
    required: false
    default: '[]'
outputs:
  allow-invocation:
    value: ${{ steps.budget.outputs.allow-invocation }}
  decision:
    value: ${{ steps.budget.outputs.decision }}
  round:
    value: ${{ steps.budget.outputs.round }}
  invocation-key:
    value: ${{ steps.budget.outputs.invocation-key }}
  checkpoint-sha256:
    value: ${{ steps.budget.outputs.checkpoint-sha256 }}
  comment-id:
    value: ${{ steps.budget.outputs.comment-id }}
runs:
  using: composite
  steps:
    - id: budget
      shell: bash
      env:
        GH_TOKEN: ${{ inputs.github-token }}
        BUDGET_MODE: ${{ inputs.mode }}
        REVIEWER: ${{ inputs.reviewer }}
        PR_NUMBER: ${{ inputs.pr-number }}
        EXPECTED_HEAD_SHA: ${{ inputs.expected-head-sha }}
        FULL_DIFF_SHA256: ${{ inputs.full-diff-sha256 }}
        DIFF_MODE: ${{ inputs.diff-mode }}
        FORCE_REVIEW: ${{ inputs.force-review }}
        INPUT_FILES_JSON: ${{ inputs.input-files-json }}
        AUTHENTICATED_REVIEW_JSON: ${{ inputs.authenticated-review-json }}
        MODEL_ROUTE_JSON: ${{ inputs.model-route-json }}
        EFFORT: ${{ inputs.effort }}
        ACTUAL_CALL_COUNT: ${{ inputs.actual-call-count }}
        ELAPSED_SECONDS: ${{ inputs.elapsed-seconds }}
        REVIEW_OUTCOME: ${{ inputs.outcome }}
        STOP_REASON: ${{ inputs.stop-reason }}
        REMAINING_FINDING_IDS_JSON: ${{ inputs.remaining-finding-ids-json }}
        CHECKPOINT_FILE: ${{ inputs.checkpoint-file }}
      run: |
        set -euo pipefail
        umask 077
        budget_dir="$(mktemp -d "$RUNNER_TEMP/review-budget.XXXXXX")"
        chmod 0700 "$budget_dir"
        trap 'rm -rf -- "$budget_dir"' EXIT

        publish_outputs() {
          local mutation_response="${1:-}"
          cat -- "$budget_dir/summary.md" >> "$GITHUB_STEP_SUMMARY"
          python3 - "$budget_dir/output.json" "$mutation_response" "$GITHUB_OUTPUT" <<'PY'
        import json
        import re
        import sys
        from pathlib import Path

        output = json.loads(Path(sys.argv[1]).read_text())
        comment_id = output["comment-id"]
        if sys.argv[2]:
            mutation = json.loads(Path(sys.argv[2]).read_text())
            value = mutation.get("id")
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SystemExit("invalid mutation comment id")
            comment_id = str(value)
        values = {
            "allow-invocation": output["allow-invocation"],
            "decision": output["decision"],
            "round": output["round"],
            "invocation-key": output["invocation-key"],
            "checkpoint-sha256": output["checkpoint-sha256"],
            "comment-id": comment_id,
        }
        patterns = {
            "allow-invocation": r"(?:true|false)",
            "decision": r"[a-z_]+",
            "round": r"[0-9]*",
            "invocation-key": r"[0-9:]*",
            "checkpoint-sha256": r"[0-9a-f]{64}",
            "comment-id": r"[0-9]*",
        }
        with Path(sys.argv[3]).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                if not isinstance(value, str) or re.fullmatch(patterns[key], value) is None:
                    raise SystemExit(f"invalid output: {key}")
                stream.write(f"{key}={value}\n")
        PY
        }

        python3 - "$budget_dir/request.json" \
          "$BUDGET_MODE" "$GITHUB_REPOSITORY" "$PR_NUMBER" "$REVIEWER" \
          "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" "$EXPECTED_HEAD_SHA" \
          "$FULL_DIFF_SHA256" "$DIFF_MODE" "$INPUT_FILES_JSON" \
          "$FORCE_REVIEW" \
          "$AUTHENTICATED_REVIEW_JSON" "$MODEL_ROUTE_JSON" "$EFFORT" \
          "$ACTUAL_CALL_COUNT" "$ELAPSED_SECONDS" "$REVIEW_OUTCOME" \
          "$STOP_REASON" "$REMAINING_FINDING_IDS_JSON" "$CHECKPOINT_FILE" \
          "$GITHUB_WORKSPACE" "$GITHUB_SERVER_URL" <<'PY'
        import json
        import sys
        from pathlib import Path

        names = (
            "operation", "repository", "pr", "reviewer", "run_id", "run_attempt",
            "head_sha", "full_diff_sha256", "diff_mode", "input_files_json",
            "force_review",
            "authenticated_review_json", "model_route_json", "effort", "actual_call_count",
            "elapsed_seconds", "outcome", "stop_reason", "remaining_finding_ids_json",
            "checkpoint_file", "github_workspace", "server_url",
        )
        destination = Path(sys.argv[1])
        payload = dict(zip(names, sys.argv[2:], strict=True))
        destination.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        destination.chmod(0o600)
        PY

        python3 "$GITHUB_ACTION_PATH/review_invocation_budget.py" preflight \
          --request-file "$budget_dir/request.json" \
          --comments-file "$budget_dir/comments.json" \
          --output-directory "$budget_dir"
        transport_ready="$(python3 - "$budget_dir/preflight.json" <<'PY'
        import json
        import sys
        from pathlib import Path

        print("true" if json.loads(Path(sys.argv[1]).read_text())["continue"] else "false")
        PY
        )"
        if [[ "$transport_ready" != "true" ]]; then
          publish_outputs ""
          exit 0
        fi

        gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" > "$budget_dir/pr.json"
        gh api --paginate "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments?per_page=100" > "$budget_dir/comments.json"
        gh api --paginate -H 'Accept: application/vnd.github+json' "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/timeline?per_page=100" > "$budget_dir/timeline.json"

        python3 "$GITHUB_ACTION_PATH/review_invocation_budget.py" list-run-identities \
          --request-file "$budget_dir/request.json" \
          --comments-file "$budget_dir/comments.json" \
          --output-directory "$budget_dir"

        while IFS=$'\t' read -r run_id run_attempt; do
          [[ "$run_id" =~ ^[1-9][0-9]*$ ]] || exit 2
          [[ "$run_attempt" =~ ^[1-9][0-9]*$ ]] || exit 2
          gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}/attempts/${run_attempt}" \
            > "$budget_dir/run-${run_id}-${run_attempt}.json"
        done < <(python3 - "$budget_dir/run-identities.json" <<'PY'
        import json
        import sys
        from pathlib import Path

        payload = json.loads(Path(sys.argv[1]).read_text())
        for item in payload["runs"]:
            print(f'{item["run_id"]}\t{item["run_attempt"]}')
        PY
        )

        while IFS=$'\t' read -r actor_index encoded_actor; do
          [[ "$actor_index" =~ ^[0-9]+$ ]] || exit 2
          [[ "$encoded_actor" =~ ^[A-Za-z0-9._~%+-]+$ ]] || exit 2
          gh api "repos/${GITHUB_REPOSITORY}/collaborators/${encoded_actor}/permission" \
            > "$budget_dir/permission-${actor_index}.json"
        done < <(python3 - "$budget_dir/run-identities.json" <<'PY'
        import json
        import sys
        from pathlib import Path

        payload = json.loads(Path(sys.argv[1]).read_text())
        for item in payload["permission_actors"]:
            print(f'{item["index"]}\t{item["encoded_login"]}')
        PY
        )

        python3 "$GITHUB_ACTION_PATH/review_invocation_budget.py" "$BUDGET_MODE" \
          --request-file "$budget_dir/request.json" \
          --comments-file "$budget_dir/comments.json" \
          --output-directory "$budget_dir"

        readarray -t budget_fields < <(python3 - "$budget_dir/output.json" <<'PY'
        import json
        import sys
        from pathlib import Path

        payload = json.loads(Path(sys.argv[1]).read_text())
        print(payload["mutation"])
        print(payload["prior-comment-id"])
        PY
        )
        mutation="${budget_fields[0]}"
        prior_comment_id="${budget_fields[1]}"
        mutation_response=""

        if [[ "$mutation" != "none" ]]; then
          gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" > "$budget_dir/cas-pr.json"
          if [[ "$mutation" == "patch" ]]; then
            [[ "$prior_comment_id" =~ ^[1-9][0-9]*$ ]] || exit 2
            gh api "repos/${GITHUB_REPOSITORY}/issues/comments/${prior_comment_id}" \
              > "$budget_dir/cas-comment.json"
          elif [[ "$mutation" == "create" ]]; then
            gh api --paginate "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments?per_page=100" \
              > "$budget_dir/cas-comments.json"
          else
            exit 2
          fi

          if ! python3 - "$budget_dir/output.json" "$budget_dir/cas-pr.json" \
              "$budget_dir/cas-comment.json" "$budget_dir/cas-comments.json" <<'PY'
        import json
        import sys
        from pathlib import Path

        def pages(path):
            text = path.read_text()
            decoder = json.JSONDecoder()
            cursor = 0
            decoded_pages = 0
            values = []
            while cursor < len(text):
                while cursor < len(text) and text[cursor].isspace():
                    cursor += 1
                if cursor == len(text):
                    break
                page, cursor = decoder.raw_decode(text, cursor)
                if not isinstance(page, list):
                    raise SystemExit(1)
                decoded_pages += 1
                values.extend(page)
            if decoded_pages == 0:
                raise SystemExit(1)
            return values

        expected = json.loads(Path(sys.argv[1]).read_text())
        pull = json.loads(Path(sys.argv[2]).read_text())
        if pull.get("head", {}).get("sha") != expected["expected-head-sha"]:
            raise SystemExit(1)
        if expected["mutation"] == "patch":
            current = json.loads(Path(sys.argv[3]).read_text())
            if (
                current.get("id") != int(expected["prior-comment-id"])
                or current.get("body") != expected["prior-comment-body"]
                or current.get("user", {}).get("login") != "github-actions[bot]"
            ):
                raise SystemExit(1)
        else:
            marker = expected["marker"]
            if any(
                isinstance(item, dict) and isinstance(item.get("body"), str)
                and (item["body"] == marker or item["body"].startswith(marker + "\n"))
                for item in pages(Path(sys.argv[4]))
            ):
                raise SystemExit(1)
        PY
          then
            python3 "$GITHUB_ACTION_PATH/review_invocation_budget.py" cas-failed \
              --request-file "$budget_dir/request.json" \
              --comments-file "$budget_dir/comments.json" \
              --output-directory "$budget_dir"
            mutation="none"
          fi
        fi

        if [[ "$mutation" == "create" ]]; then
          mutation_response="$budget_dir/mutation.json"
          gh api --method POST "repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
            --input "$budget_dir/comment-payload.json" > "$mutation_response"
        elif [[ "$mutation" == "patch" ]]; then
          mutation_response="$budget_dir/mutation.json"
          gh api --method PATCH "repos/${GITHUB_REPOSITORY}/issues/comments/${prior_comment_id}" \
            --input "$budget_dir/comment-payload.json" > "$mutation_response"
        fi

        publish_outputs "$mutation_response"
""",
    Loader=yaml.BaseLoader,
)
def _budget_action_with_round_variable(action: dict) -> dict:
    """Derive the v1.60 budget action: one optional input bridged to the helper env.

    Stating the delta keeps the two expectations from drifting apart, which a second
    verbatim copy of the action document would invite.
    """

    updated = deepcopy(action)
    updated["inputs"]["max-rounds"] = {"required": "false", "default": ""}
    for step in updated["runs"]["steps"]:
        if isinstance(step, dict) and isinstance(step.get("env"), dict):
            step["env"]["REVIEW_MAX_ROUNDS"] = "${{ inputs.max-rounds }}"
            break
    else:
        raise ValueError("budget action has no environment block")
    return updated


EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_V160 = _budget_action_with_round_variable(
    EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION
)


def _budget_action_with_dismissals(action: dict) -> dict:
    """Derive the v1.63 budget action: one output naming the dismissed finding IDs.

    The publisher inside the run block gains the matching value and its closed pattern;
    every other byte of the action is the v1.60 document.
    """

    updated = deepcopy(action)
    updated["outputs"]["dismissed-finding-ids"] = {
        "value": "${{ steps.budget.outputs.dismissed-finding-ids }}"
    }
    step = updated["runs"]["steps"][0]
    for anchor, addition in (
        (
            '    "comment-id": comment_id,\n}\n',
            '    "dismissed-finding-ids": output["dismissed-finding-ids"],\n',
        ),
        (
            '    "comment-id": r"[0-9]*",\n}\n',
            '    "dismissed-finding-ids": r"(?:RVW-[0-9a-f]{12}(?:,RVW-[0-9a-f]{12})*)?",\n',
        ),
    ):
        if step["run"].count(anchor) != 1:
            raise ValueError("budget action run block differs")
        step["run"] = step["run"].replace(anchor, anchor[: -len("}\n")] + addition + "}\n")
    return updated


EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_V163 = _budget_action_with_dismissals(
    EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_V160
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
    "review_execution",
    "reviewer",
    "run_attempt",
    "run_id",
    "schema",
    "successful_head",
)
LEGACY_QUALITY_STATE_KEYS = tuple(
    key for key in QUALITY_STATE_KEYS if key != "review_execution"
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
        "upsert_sha256_v147": (
            "f23f444b3a9c7b04707779f3f8431da60107c8792558c02f5c011216ca93d805"
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
        "upsert_sha256_v147": (
            "62f29ebd7ca5fe47f4449cfa43a03ebb5a04e6b051a170b2221a670986c04ea8"
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


class _DuplicateKeyRejectingLoader(yaml.BaseLoader):
    """Preserve BaseLoader scalars while rejecting every duplicate mapping key."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        mapping: dict = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _load_release_yaml(
    payload: str | bytes, *, reject_duplicate_keys: bool
) -> object:
    loader = (
        _DuplicateKeyRejectingLoader
        if reject_duplicate_keys
        else yaml.BaseLoader
    )
    try:
        return yaml.load(payload, Loader=loader)
    except yaml.YAMLError:
        if not reject_duplicate_keys:
            raise
        raise ReleaseVerificationError("v1.51 release YAML is invalid") from None


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
    current_diagnostics_contract = workflow_sha256 in {
        EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256["opencode"],
        EXPECTED_REVIEW_POLICY_WORKFLOW_SHA256["opencode"],
        EXPECTED_REVIEW_ROUNDS_VARIABLE_WORKFLOW_SHA256["opencode"],
        EXPECTED_SAME_HEAD_CANCEL_WORKFLOW_SHA256["opencode"],
        EXPECTED_FILTER_REASON_WORKFLOW_SHA256["opencode"],
        EXPECTED_LABEL_MISMATCH_WORKFLOW_SHA256["opencode"],
        EXPECTED_OPENCODE_FINDING_ID_WORKFLOW_SHA256["opencode"],
        EXPECTED_OPENCODE_DISMISSAL_WORKFLOW_SHA256["opencode"],
    }
    initial_validation_argument = " initial" if current_diagnostics_contract else ""
    repair_validation_argument = " repair" if current_diagnostics_contract else ""
    format_sequence = (
        'run_opencode "$initial_prompt" "$RUNNER_TEMP/opencode-review.jsonl"',
        'if ! candidate_outer_format_valid "$candidate_dir/review.md"'
        f'{initial_validation_argument}; then',
        '"$repair_prompt" "$RUNNER_TEMP/opencode-format-repair.jsonl"',
        'if ! candidate_outer_format_valid "$candidate_dir/review-repaired.md"'
        f'{repair_validation_argument}; then',
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
        workflow_sha256 in {
            OPENCODE_AUTO_REVIEW_SHA256,
            EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256["opencode"],
            EXPECTED_REVIEW_POLICY_WORKFLOW_SHA256["opencode"],
            EXPECTED_REVIEW_ROUNDS_VARIABLE_WORKFLOW_SHA256["opencode"],
            EXPECTED_SAME_HEAD_CANCEL_WORKFLOW_SHA256["opencode"],
            EXPECTED_FILTER_REASON_WORKFLOW_SHA256["opencode"],
            EXPECTED_LABEL_MISMATCH_WORKFLOW_SHA256["opencode"],
            EXPECTED_OPENCODE_FINDING_ID_WORKFLOW_SHA256["opencode"],
            EXPECTED_OPENCODE_DISMISSAL_WORKFLOW_SHA256["opencode"],
        }
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
        and _opencode_review_run_sha256(run_script)
        in {OPENCODE_REVIEW_RUN_SHA256, OPENCODE_REVIEW_RUN_V147_SHA256}
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
        document = _load_release_yaml(
            tree.read_text(path),
            reject_duplicate_keys=release_supports_review_policy(ref),
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
        document = _load_release_yaml(
            tree.read_text(path),
            reject_duplicate_keys=release_supports_review_policy(ref),
        )
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


def require_review_policy_helper_contract(
    source: str, optin: bool, decline: bool = False
) -> None:
    """Authenticate and statically validate the public policy-helper surface."""

    expected_digest = (
        EXPECTED_REVIEW_POLICY_HELPER_SHA256_V166
        if decline
        else EXPECTED_REVIEW_POLICY_HELPER_SHA256_V159
        if optin
        else EXPECTED_REVIEW_POLICY_HELPER_SHA256
    )
    expected_literals = (
        EXPECTED_REVIEW_POLICY_LITERALS_V159
        if optin
        else EXPECTED_REVIEW_POLICY_LITERALS
    )
    try:
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != expected_digest:
            raise ValueError("helper source digest differs")
        compile(
            source,
            REVIEW_POLICY_HELPER_ROOT.path.as_posix(),
            "exec",
            dont_inherit=True,
        )
        module = ast.parse(
            source, filename=REVIEW_POLICY_HELPER_ROOT.path.as_posix()
        )
        _require_unique_module_bindings(
            module,
            frozenset(
                {
                    "PolicyError",
                    *EXPECTED_REVIEW_POLICY_RECORDS,
                    "resolve_policy",
                }
            ),
        )
        error = _class_node(module, "PolicyError")
        if [ast.unparse(base) for base in error.bases] != ["ValueError"]:
            raise ValueError("policy error base differs")
        for name, expected in EXPECTED_REVIEW_POLICY_RECORDS.items():
            if _record_fields(_class_node(module, name)) != expected:
                raise ValueError("policy record differs")
        functions = _module_function_headers(module)
        if functions.get("resolve_policy") != (
            "def resolve_policy(request: PolicyRequest) -> PolicyDecision"
        ):
            raise ValueError("policy function differs")
        direct_literals = expected_literals - {
            "workflow_auto_false",
            "review_auto_false",
        }
        if any(literal not in source for literal in direct_literals):
            raise ValueError("policy literal differs")
        automatic = _function_node(module, "_automatic_decision")
        automatic_source = ast.unparse(automatic)
        if (
            "workflow_auto_{str(automatic).lower()}"
            not in automatic_source
            or "review_auto_{str(automatic).lower()}"
            not in automatic_source
        ):
            raise ValueError("automatic policy reasons differ")
    except (SyntaxError, TypeError, UnicodeError, ValueError):
        raise ReleaseVerificationError(
            "review-policy helper contract is invalid"
        ) from None


def _verify_review_policy_action(
    tree: VerifiedCommitTree, ref: str
) -> None:
    if not release_supports_review_policy(ref):
        return
    expected_files = {
        (REVIEW_POLICY_ACTION_ROOT.path.as_posix(), "100644", "blob"),
        (REVIEW_POLICY_HELPER_ROOT.path.as_posix(), "100644", "blob"),
    }
    actual_files = {
        (entry.path.as_posix(), entry.mode, entry.object_type)
        for entry in tree.files(REVIEW_POLICY_ACTION_ROOT.path.parent)
    }
    if actual_files != expected_files:
        raise ReleaseVerificationError("review-policy inventory is not closed")
    try:
        action = _load_release_yaml(
            tree.read_file(REVIEW_POLICY_ACTION_ROOT.path),
            reject_duplicate_keys=True,
        )
        if (
            action != EXPECTED_REVIEW_POLICY_ACTION
            or tuple(action["inputs"])
            != tuple(EXPECTED_REVIEW_POLICY_ACTION["inputs"])
            or tuple(action["outputs"])
            != tuple(EXPECTED_REVIEW_POLICY_ACTION["outputs"])
        ):
            raise ValueError("action differs")
    except (
        AttributeError,
        ReleaseVerificationError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ):
        raise ReleaseVerificationError(
            "review-policy action contract is invalid"
        ) from None
    try:
        helper = tree.read_file(REVIEW_POLICY_HELPER_ROOT.path).decode("utf-8")
    except (ReleaseVerificationError, UnicodeDecodeError):
        raise ReleaseVerificationError(
            "review-policy helper contract is invalid"
        ) from None
    require_review_policy_helper_contract(
        helper,
        release_supports_review_optin(ref),
        release_supports_label_mismatch_decline(ref),
    )


def _verify_canonicalize_review_helpers(tree: VerifiedCommitTree, ref: str) -> None:
    canonical_path = CANONICALIZE_REVIEW_HELPER_ROOT.path.as_posix()
    scope_path = REVIEW_SCOPE_HELPER_ROOT.path.as_posix()
    try:
        canonical_source = tree.read_file(canonical_path)
        scope_source = tree.read_file(scope_path)
        if (
            hashlib.sha256(canonical_source).hexdigest()
            != (
                EXPECTED_CANONICALIZE_REVIEW_HELPER_SHA256_V163
                if release_supports_finding_dismissal(ref)
                else EXPECTED_CANONICALIZE_REVIEW_HELPER_SHA256_V162
                if release_supports_filter_reason_surface(ref)
                else EXPECTED_CANONICALIZE_REVIEW_HELPER_SHA256
            )
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

        dismissals = release_supports_finding_dismissal(ref)
        canonical_literals = {
            "HARD_REASONS": EXPECTED_CANONICALIZER_HARD_REASONS,
            "SOFT_REASONS": (
                EXPECTED_CANONICALIZER_SOFT_REASONS_V163
                if dismissals
                else EXPECTED_CANONICALIZER_SOFT_REASONS
            ),
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

        expected_records = (
            EXPECTED_CANONICALIZER_RECORDS_V163 if dismissals else EXPECTED_CANONICALIZER_RECORDS
        )
        for name, expected in expected_records.items():
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


def _verify_canonicalize_review_action(
    tree: VerifiedCommitTree, ref: str
) -> None:
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
        document = _load_release_yaml(
            tree.read_text(path),
            reject_duplicate_keys=release_supports_review_policy(ref),
        )
    except (ReleaseVerificationError, yaml.YAMLError):
        raise ReleaseVerificationError(
            "canonicalize-review action contract is invalid"
        ) from None
    expected_action = (
        EXPECTED_CANONICALIZE_REVIEW_ACTION_V163
        if release_supports_finding_dismissal(ref)
        else EXPECTED_CANONICALIZE_REVIEW_ACTION_V162
        if release_supports_filter_reason_surface(ref)
        else EXPECTED_CANONICALIZE_REVIEW_ACTION
    )
    if document != expected_action:
        raise ReleaseVerificationError(
            "canonicalize-review action contract is invalid"
        )
    _verify_canonicalize_review_helpers(tree, ref)


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
                document = _load_release_yaml(
                    text,
                    reject_duplicate_keys=release_supports_review_policy(ref),
                )
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
    # v1.68 withdrew the workflow_dispatch pull-request review, so its contract
    # describes a file the release no longer ships.
    retired = (
        {"gemini-pr-review.yml"} if release_retires_manual_pr_review(ref) else set()
    )
    for filename, contract in MANUAL_GEMINI_FETCH_CONTRACTS.items():
        if filename in retired:
            continue
        path = f"{root}/{filename}"
        try:
            document = _load_release_yaml(
                tree.read_text(path),
                reject_duplicate_keys=release_supports_review_policy(ref),
            )
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


def _function_node(module: ast.Module | ast.ClassDef, name: str) -> ast.FunctionDef:
    matches = [
        item
        for item in module.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    ]
    if len(matches) != 1:
        raise ValueError(f"function {name} differs")
    return matches[0]


def _ast_expression_matches(node: ast.AST, expression: str) -> bool:
    expected = ast.parse(expression, mode="eval").body
    return ast.dump(node, include_attributes=False) == ast.dump(
        expected, include_attributes=False
    )


def _ast_statement_matches(node: ast.AST, statement: str) -> bool:
    expected = ast.parse(statement).body
    return len(expected) == 1 and ast.dump(
        node, include_attributes=False
    ) == ast.dump(expected[0], include_attributes=False)


def _record_shape(
    node: ast.ClassDef,
) -> tuple[tuple[str, str, str | None], ...]:
    if (
        len(node.decorator_list) != 1
        or not _ast_expression_matches(
            node.decorator_list[0], "dataclass(frozen=True)"
        )
    ):
        raise ValueError(f"record {node.name} decorator differs")
    return tuple(
        (
            item.target.id,
            ast.dump(item.annotation, include_attributes=False),
            None
            if item.value is None
            else ast.dump(item.value, include_attributes=False),
        )
        for item in node.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    )


def _expected_record_shape(
    fields: tuple[tuple[str, str, str | None], ...],
) -> tuple[tuple[str, str, str | None], ...]:
    return tuple(
        (
            name,
            ast.dump(ast.parse(annotation, mode="eval").body, include_attributes=False),
            None
            if default is None
            else ast.dump(ast.parse(default, mode="eval").body, include_attributes=False),
        )
        for name, annotation, default in fields
    )


def _raise_reason(statement: ast.stmt, exception: str, reason: str) -> bool:
    if not isinstance(statement, ast.Raise) or not isinstance(statement.exc, ast.Call):
        return False
    call = statement.exc
    return (
        isinstance(call.func, ast.Name)
        and call.func.id == exception
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == reason
        and not call.keywords
    )


def _refusal_return(statement: ast.stmt, state: str, reason: str) -> bool:
    if not isinstance(statement, ast.Return) or not isinstance(statement.value, ast.Call):
        return False
    call = statement.value
    return (
        isinstance(call.func, ast.Name)
        and call.func.id == "refuse"
        and len(call.args) == 3
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == state
        and isinstance(call.args[1], ast.Name)
        and call.args[1].id == "request"
        and isinstance(call.args[2], ast.Constant)
        and call.args[2].value == reason
        and not call.keywords
    )


def _direct_guard(
    statements: list[ast.stmt], expression: str, exception: str, reason: str
) -> ast.If:
    matches = [
        statement
        for statement in statements
        if isinstance(statement, ast.If)
        and _ast_expression_matches(statement.test, expression)
    ]
    if (
        len(matches) != 1
        or len(matches[0].body) != 1
        or matches[0].orelse
        or not _raise_reason(matches[0].body[0], exception, reason)
    ):
        raise ValueError(f"live guard {reason} differs")
    return matches[0]


def _heredoc_python(script: str, declaration: str, terminator: str) -> ast.Module:
    lines = script.splitlines()
    starts = [index for index, line in enumerate(lines) if line == declaration]
    if len(starts) != 1:
        raise ValueError("embedded Python declaration differs")
    endings = [
        index
        for index in range(starts[0] + 1, len(lines))
        if lines[index] == terminator
    ]
    if len(endings) != 1:
        raise ValueError("embedded Python terminator differs")
    return ast.parse("\n".join(lines[starts[0] + 1 : endings[0]]) + "\n")


class _ShellCommand(NamedTuple):
    text: str
    controls: tuple[str, ...]


class _ShellFunctionDefinition(NamedTuple):
    name: str
    declaration: str
    controls: tuple[str, ...]
    commands: list[_ShellCommand]
    start: int
    end: int | None


class _ShellFrame(NamedTuple):
    kind: str
    label: str
    function_index: int | None = None


class _ShellFunctionAnalysis(NamedTuple):
    commands: tuple[_ShellCommand, ...]
    invocations: tuple[_ShellCommand, ...]


def _shell_logical_lines(script: str) -> tuple[str, ...]:
    """Tokenize shell commands while excluding authenticated heredoc data."""

    physical = script.splitlines()
    logical: list[str] = []
    index = 0
    while index < len(physical):
        line = physical[index].strip()
        index += 1
        if not line or line.startswith("#"):
            continue
        parts = [line]
        while parts[-1].endswith("\\"):
            if index >= len(physical):
                raise ValueError("shell continuation is unterminated")
            parts[-1] = parts[-1][:-1].rstrip()
            parts.append(physical[index].strip())
            index += 1
        command = " ".join(parts)
        logical.append(command)
        for terminator in _shell_heredoc_terminators(command):
            while (
                index < len(physical)
                and physical[index].strip() != terminator
            ):
                index += 1
            if index >= len(physical):
                raise ValueError("shell heredoc is unterminated")
            index += 1
    return tuple(logical)


_SHELL_HEREDOC_TERMINATOR_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*\Z"
)


def _shell_heredoc_terminators(command: str) -> tuple[str, ...]:
    """Return delimiters following real, unquoted shell ``<<`` operators."""

    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.commenters = "#"
    lexer.whitespace_split = True
    tokens = tuple(lexer)
    terminators: list[str] = []
    for index, token in enumerate(tokens):
        if token != "<<":
            continue
        if index + 1 >= len(tokens) or not _SHELL_HEREDOC_TERMINATOR_PATTERN.fullmatch(
            tokens[index + 1]
        ):
            raise ValueError("shell heredoc delimiter differs")
        terminators.append(tokens[index + 1])
    return tuple(terminators)


_SHELL_DECLARATION_PATTERN = re.compile(
    r"(?:(?P<canonical>[A-Za-z_][A-Za-z0-9_]*)"
    r"[ \t]*\([ \t]*\)|"
    r"function[ \t]+(?P<alternate>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:[ \t]*\([ \t]*\))?)[ \t]*\{\Z"
)
_SHELL_DECLARATION_HEAD_PATTERN = re.compile(
    r"(?:(?P<canonical>[A-Za-z_][A-Za-z0-9_]*)"
    r"[ \t]*\([ \t]*\)|"
    r"function[ \t]+(?P<alternate>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:[ \t]*\([ \t]*\))?)\Z"
)
_SHELL_DECLARATION_PREFIX_PATTERN = re.compile(
    r"(?:(?P<canonical>[A-Za-z_][A-Za-z0-9_]*)"
    r"[ \t]*\([ \t]*\)|"
    r"function[ \t]+(?P<alternate>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:[ \t]*\([ \t]*\))?)[ \t]*\{"
)
_SHELL_ASSIGNMENT_WORD_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\+?=).*\Z"
)
_SHELL_STATIC_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<append>\+)?="
    r"(?P<value>[A-Za-z0-9_./:+-]*)\Z"
)


def _shell_semicolon_statements(line: str) -> tuple[str, ...]:
    """Split executable semicolon lists without splitting quoted shell data."""

    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    escaped = False
    command_substitution_depth = 0
    parameter_expansion_depth = 0
    while index < len(line):
        character = line[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if character == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote == "'":
            if character == "'":
                quote = None
            index += 1
            continue
        if quote == "`":
            if character == "`":
                quote = None
            index += 1
            continue
        if character == "`" and quote is None:
            quote = "`"
            index += 1
            continue
        if character == "'" and quote is None:
            quote = "'"
            index += 1
            continue
        if character == '"':
            quote = None if quote == '"' else '"'
            index += 1
            continue
        if character == "$" and index + 1 < len(line):
            following = line[index + 1]
            if following == "(":
                command_substitution_depth += 1
                index += 2
                continue
            if following == "{":
                parameter_expansion_depth += 1
                index += 2
                continue
        if command_substitution_depth and character == ")":
            command_substitution_depth -= 1
            index += 1
            continue
        if parameter_expansion_depth and character == "}":
            parameter_expansion_depth -= 1
            index += 1
            continue
        if (
            character != ";"
            or quote is not None
            or command_substitution_depth
            or parameter_expansion_depth
        ):
            index += 1
            continue

        statement = line[start:index].strip()
        lookahead = index + 1
        while lookahead < len(line) and line[lookahead].isspace():
            lookahead += 1
        control_keyword = None
        for keyword in ("then", "do"):
            end = lookahead + len(keyword)
            if (
                line[lookahead:end] == keyword
                and (end == len(line) or line[end].isspace())
            ):
                control_keyword = keyword
                lookahead = end
                break
        if control_keyword is not None:
            statements.append(f"{statement}; {control_keyword}")
            while lookahead < len(line) and line[lookahead].isspace():
                lookahead += 1
            start = lookahead
            index = lookahead
            continue
        if statement:
            statements.append(statement)
        start = index + 1
        index += 1

    if quote is not None or command_substitution_depth or parameter_expansion_depth:
        raise ValueError("shell statement has unterminated lexical context")
    remainder = line[start:].strip()
    if remainder:
        statements.append(remainder)
    return tuple(statements)


def _expand_shell_declaration(statement: str) -> tuple[str, ...]:
    """Separate a declaration's opening brace from its compact first command."""

    declaration = _SHELL_DECLARATION_PREFIX_PATTERN.match(statement)
    if declaration is None:
        return (statement,)
    head = statement[: declaration.end()].strip()
    remainder = statement[declaration.end() :].strip()
    if not remainder:
        return (head,)
    return (head, *_expand_shell_declaration(remainder))


def _shell_statements(script: str) -> tuple[str, ...]:
    """Tokenize executable statements, including compact function bodies."""

    statements: list[str] = []
    for line in _shell_logical_lines(script):
        for statement in _shell_semicolon_statements(line):
            statements.extend(_expand_shell_declaration(statement))
    return tuple(statements)


def _shell_identifier_tokens(words: Iterable[str]) -> tuple[str, ...]:
    """Return identifier tokens from already parsed shell words."""

    identifiers: list[str] = []
    for word in words:
        index = 0
        while index < len(word):
            character = word[index]
            if character.isalpha() or character == "_":
                end = index + 1
                while end < len(word) and (
                    word[end].isalnum() or word[end] == "_"
                ):
                    end += 1
                identifiers.append(word[index:end])
                index = end
                continue
            index += 1
    return tuple(identifiers)


def _shell_command_words(statement: str) -> tuple[str, ...]:
    lexer = shlex.shlex(statement, posix=True)
    lexer.commenters = ""
    lexer.whitespace_split = True
    return tuple(lexer)


def _shell_function_analysis(script: str, name: str) -> _ShellFunctionAnalysis:
    """Parse a whole shell program and bind one top-level function to later calls."""

    lines = _shell_statements(script)
    command_pattern = re.compile(rf"{re.escape(name)}(?:[ \t]+|\Z)")
    stack: list[_ShellFrame] = []
    definitions: list[_ShellFunctionDefinition] = []
    invocation_records: list[tuple[int, _ShellCommand]] = []
    static_assignments: dict[str, str] = {}

    def active_target() -> tuple[int, int] | None:
        for stack_index in range(len(stack) - 1, -1, -1):
            frame = stack[stack_index]
            if frame.kind != "function" or frame.function_index is None:
                continue
            if definitions[frame.function_index].name == name:
                return stack_index, frame.function_index
        return None

    consumed_declaration_opening: int | None = None
    for index, line in enumerate(lines):
        if index == consumed_declaration_opening:
            continue
        declaration = _SHELL_DECLARATION_PATTERN.fullmatch(line)
        declaration_text = line
        if declaration is None:
            declaration_head = _SHELL_DECLARATION_HEAD_PATTERN.fullmatch(line)
            if (
                declaration_head is not None
                and index + 1 < len(lines)
                and lines[index + 1] == "{"
            ):
                declaration = declaration_head
                declaration_text = f"{line}\n{{"
                consumed_declaration_opening = index + 1
        if declaration is not None:
            function_name = declaration.group("canonical") or declaration.group(
                "alternate"
            )
            function_index = len(definitions)
            definitions.append(
                _ShellFunctionDefinition(
                    function_name,
                    declaration_text,
                    tuple(frame.label for frame in stack),
                    [],
                    index,
                    None,
                )
            )
            stack.append(
                _ShellFrame(
                    "function", f"function:{function_name}", function_index
                )
            )
            continue

        if line == "fi":
            if not stack or stack[-1].kind != "if":
                raise ValueError("shell program has unmatched fi")
            stack.pop()
            continue
        if line in {"else"} or line.startswith("elif "):
            if not stack or stack[-1].kind != "if":
                raise ValueError("shell program has unmatched conditional branch")
            if line.startswith("elif ") and not line.endswith("; then"):
                raise ValueError("shell program has malformed elif")
            opened = stack[-1].label.split(":", 1)[-1]
            stack[-1] = _ShellFrame("if", f"branch:{opened}:{line}")
            continue
        if line == "done":
            if not stack or stack[-1].kind != "loop":
                raise ValueError("shell program has unmatched done")
            stack.pop()
            continue
        if line == "esac":
            if not stack or stack[-1].kind != "case":
                raise ValueError("shell program has unmatched esac")
            stack.pop()
            continue
        if line == "}" or line.startswith("} "):
            if not stack or stack[-1].kind not in {"function", "group"}:
                raise ValueError("shell program closes the wrong control")
            frame = stack.pop()
            if frame.kind == "function":
                if line != "}" or frame.function_index is None:
                    raise ValueError("shell function closure differs")
                definition = definitions[frame.function_index]
                definitions[frame.function_index] = definition._replace(end=index)
            continue

        target = active_target()
        if target is not None:
            stack_index, function_index = target
            definitions[function_index].commands.append(
                _ShellCommand(
                    line,
                    tuple(frame.label for frame in stack[stack_index + 1 :]),
                )
            )
        if command_pattern.match(line):
            invocation_records.append(
                (
                    index,
                    _ShellCommand(line, tuple(frame.label for frame in stack)),
                )
            )
        else:
            words = _shell_command_words(line)
            unwrapped_words = list(words)
            assignment_words: list[str] = []
            while unwrapped_words and _SHELL_ASSIGNMENT_WORD_PATTERN.fullmatch(
                unwrapped_words[0]
            ):
                assignment_words.append(unwrapped_words.pop(0))
            if not unwrapped_words:
                for assignment_word in assignment_words:
                    assignment = _SHELL_STATIC_ASSIGNMENT_PATTERN.fullmatch(
                        assignment_word
                    )
                    if assignment is None:
                        assigned_name = assignment_word.split("=", 1)[0].rstrip(
                            "+"
                        )
                        static_assignments.pop(assigned_name, None)
                        continue
                    assigned_name = assignment.group("name")
                    assigned_value = assignment.group("value")
                    if assignment.group("append"):
                        prior_value = static_assignments.get(assigned_name)
                        if prior_value is None:
                            continue
                        assigned_value = prior_value + assigned_value
                    static_assignments[assigned_name] = assigned_value
                    if assigned_value in {".", "eval", "source", name}:
                        raise ValueError(
                            "shell program computes dynamic namespace syntax"
                        )
            while unwrapped_words and unwrapped_words[0] in {"builtin", "command"}:
                unwrapped_words.pop(0)
                while unwrapped_words and (
                    unwrapped_words[0] == "--"
                    or unwrapped_words[0].startswith("-")
                ):
                    unwrapped_words.pop(0)
            dynamic_command = unwrapped_words[0] if unwrapped_words else ""
            if "$" in dynamic_command or "`" in dynamic_command:
                raise ValueError("shell program contains computed command syntax")
            if dynamic_command in {"alias", "unalias"} or (
                dynamic_command == "shopt"
                and "expand_aliases" in unwrapped_words[1:]
            ) or "BASH_ALIASES" in _shell_identifier_tokens(words):
                raise ValueError("shell program contains alias namespace syntax")
            if dynamic_command in {".", "eval", "source"}:
                raise ValueError("shell program contains dynamic namespace syntax")
            if name in _shell_identifier_tokens(words):
                raise ValueError(
                    f"shell program contains unrecognized {name} syntax"
                )

        if line.startswith("if ") and line.endswith("; then"):
            stack.append(_ShellFrame("if", f"if:{line}"))
        elif line == "{" or line.endswith("|| {"):
            stack.append(_ShellFrame("group", f"group:{line}"))
        elif line.startswith(("for ", "while ", "until ")) and line.endswith(
            "; do"
        ):
            stack.append(_ShellFrame("loop", f"loop:{line}"))
        elif line.startswith("case ") and line.endswith(" in"):
            stack.append(_ShellFrame("case", f"case:{line}"))
        elif line in {"do"}:
            raise ValueError("shell program has unsupported detached do")

    if stack:
        raise ValueError("shell program has unterminated control flow")
    matches = [definition for definition in definitions if definition.name == name]
    if len(matches) != 1:
        raise ValueError(f"shell function {name} is ambiguous")
    function = matches[0]
    if (
        function.declaration != f"{name}() {{"
        or function.controls
        or function.end is None
    ):
        raise ValueError(f"shell function {name} is not one live top-level definition")
    if any(index <= function.end for index, _ in invocation_records):
        raise ValueError(f"shell function {name} is invoked before its definition")
    return _ShellFunctionAnalysis(
        tuple(function.commands),
        tuple(command for _, command in invocation_records),
    )


def _local_literal(function: ast.FunctionDef, name: str) -> object:
    matches = [
        item.value
        for item in ast.walk(function)
        if isinstance(item, ast.Assign)
        and len(item.targets) == 1
        and isinstance(item.targets[0], ast.Name)
        and item.targets[0].id == name
    ]
    if len(matches) != 1:
        raise ValueError(f"local literal {name} differs")
    return ast.literal_eval(matches[0])


def require_budget_helper_contract(
    source: str,
    rounds_variable: bool = False,
    filter_reasons: bool = False,
    dismissals: bool = False,
) -> None:
    """Require the authenticated schema-1 helper and every fixed policy gate."""

    # v1.60 resolves the round budget through effective_budgets(), which renames the
    # owner of the round caps and adds three statements ahead of the ledger checks.
    rounds_owner = "budgets" if rounds_variable else "state.budgets"
    claim_rounds = (
        "effective_budgets(validated).max_rounds"
        if rounds_variable
        else "validated.budgets.max_rounds"
    )
    shape_offset = 3 if rounds_variable else 0
    try:
        compile(
            source,
            "review_invocation_budget.py",
            "exec",
            dont_inherit=True,
        )
        module = ast.parse(source, filename="review_invocation_budget.py")
        expected_literals = {
            "SCHEMA": 1,
            "STATE_PREFIX": "<!-- automation-budget-state:",
            "STATE_SUFFIX": " -->",
            "MARKERS": {
                "claude": "<!-- automation:review-invocation-budget:claude:v1 -->",
                "gemini": "<!-- automation:review-invocation-budget:gemini:v1 -->",
                "opencode": "<!-- automation:review-invocation-budget:opencode:v1 -->",
            },
            "WORKFLOWS": {
                "claude": ".github/workflows/claude-code-review.yml",
                "gemini": ".github/workflows/gemini-auto-review.yml",
                "opencode": ".github/workflows/opencode-auto-review.yml",
            },
            "CENTRAL_REPOSITORY": "jhw7500/automation",
            "_BOT_LOGIN": "github-actions[bot]",
            "_OUTCOMES": {
                "success", "provider_failure", "quality_filtered",
                "checkpoint_failure", "wall_time_exhausted",
            },
            "_CALL_UNITS": {
                "claude": "claude-code-action review session",
                "gemini": "generate_content request",
                "opencode": "opencode run session",
            },
        }
        dismissal_bindings: set[str] = set()
        if dismissals:
            # v1.63: who may dismiss, and how many dismissals and actor lookups a
            # pull request may carry, are policy and stay structurally pinned.
            expected_literals.update({
                "_DISMISS_PERMISSIONS": frozenset({"admin", "maintain", "write"}),
                "MAX_DISMISSED_FINDINGS": 16,
                "MAX_PERMISSION_ACTORS": 16,
            })
            dismissal_bindings = {
                "_DISMISS_COMMAND", "DismissEvent", "DismissedFinding",
                "parse_dismiss_command", "choose_dismissals",
            }
        _require_unique_module_bindings(
            module,
            frozenset(
                {
                    *expected_literals,
                    *dismissal_bindings,
                    "BudgetPolicy",
                    "LedgerState",
                    "ClaimRequest",
                    "FinalizeRequest",
                    "serialize_ledger",
                    "parse_ledger",
                    "estimate_input_tokens",
                    "choose_override",
                    "claim",
                    "finalize",
                    "render_checkpoint",
                    "load_checkpoint",
                }
            ),
        )
        if any(
            _static_literal(module, name) != expected
            for name, expected in expected_literals.items()
        ):
            raise ValueError("helper constants differ")
        expected_type_aliases = {
            "Outcome": (
                'Literal["success", "provider_failure", "quality_filtered", '
                '"checkpoint_failure", "wall_time_exhausted"]'
            ),
            "Decision": (
                'Literal["claimed", "finalized", "state_invalid", '
                '"diff_unavailable", "authenticated_reuse", "duplicate_head", '
                '"duplicate_effective_diff", "input_budget_exhausted", '
                '"round_budget_exhausted", "total_usage_budget_exhausted"]'
            ),
        }
        for name, expression in expected_type_aliases.items():
            bindings = [
                item.value
                for item in module.body
                if isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
                and item.targets[0].id == name
            ]
            if (
                len(bindings) != 1
                or not _ast_expression_matches(bindings[0], expression)
            ):
                raise ValueError(f"helper {name} type differs")
        policy = _class_node(module, "BudgetPolicy")
        policy_defaults = {
            item.target.id: ast.literal_eval(item.value)
            for item in policy.body
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.value is not None
        }
        if policy_defaults != {
            "max_rounds": 2,
            "max_override_rounds": 1,
            "max_calls_per_round": 1,
            "max_wall_seconds_per_round": 600,
            "max_estimated_tokens_per_round": 200_000,
            "max_estimated_tokens_total": 400_000,
        }:
            raise ValueError("budget defaults differ")
        expected_records = {
            "BudgetPolicy": (
                ("max_rounds", "int", "2"),
                ("max_override_rounds", "int", "1"),
                ("max_calls_per_round", "int", "1"),
                ("max_wall_seconds_per_round", "int", "600"),
                ("max_estimated_tokens_per_round", "int", "200_000"),
                ("max_estimated_tokens_total", "int", "400_000"),
            ),
            "RunProvenance": (
                ("repository", "str", None),
                ("pr", "int", None),
                ("head_sha", "str", None),
                ("caller_workflow_path", "str", None),
                ("caller_event", "str", None),
                ("referenced_workflow_path", "str", None),
                ("referenced_workflow_ref", "str", None),
                ("referenced_workflow_sha", "str", None),
                ("run_id", "int", None),
                ("run_attempt", "int", None),
                ("status", "str", None),
                ("conclusion", "str | None", None),
            ),
            "Invocation": (
                ("run_id", "int", None),
                ("run_attempt", "int", None),
                ("head_sha", "str", None),
                ("full_diff_sha256", "str", None),
                ("caller_workflow_path", "str", None),
                ("caller_event", "str", None),
                ("referenced_workflow_path", "str", None),
                ("referenced_workflow_ref", "str", None),
                ("referenced_workflow_sha", "str", None),
                ("round_number", "int", None),
                ("override_event_id", "int | None", None),
                ("model_route", "tuple[str, ...]", None),
                ("effort", "str", None),
                ("call_unit", "str", None),
                ("call_count", "int", None),
                ("estimated_input_tokens", "int", None),
                ("elapsed_seconds", "int", None),
                ("status", "str", None),
                ("outcome", "Outcome | None", None),
                ("stop_reason", "str", None),
                ("remaining_finding_ids", "tuple[str, ...]", None),
            ),
            "ClaimRequest": (
                ("repository", "str", None),
                ("pr", "int", None),
                ("reviewer", "Reviewer", None),
                ("run_id", "int", None),
                ("run_attempt", "int", None),
                ("head_sha", "str", None),
                ("full_diff_sha256", "str", None),
                ("estimated_input_tokens", "int", None),
                ("diff_mode", "str", None),
                ("authenticated_review", "AuthenticatedReview", None),
                ("override_events", "tuple[OverrideEvent, ...]", None),
                ("model_route", "tuple[str, ...]", None),
                ("effort", "str", None),
                ("call_unit", "str", None),
                ("force_review", "bool", "False"),
            ),
            "DecisionRecord": (
                ("decision", "str | None", "None"),
                ("stop_reason", "str | None", "None"),
                ("run_id", "int | None", "None"),
                ("run_attempt", "int | None", "None"),
            ),
            "Handoff": (
                ("repository", "str | None", "None"),
                ("pr", "int | None", "None"),
                ("reviewer", "Reviewer | None", "None"),
                ("current_head_sha", "str | None", "None"),
                ("current_full_diff_sha256", "str | None", "None"),
                ("current_run_id", "int | None", "None"),
                ("current_run_attempt", "int | None", "None"),
                ("automatic_rounds", "int | None", "None"),
                ("override_rounds", "int | None", "None"),
                ("round_usage", "tuple[tuple[int, int, int, int], ...]", "()"),
                ("decision", "str | None", "None"),
                ("outcome", "Outcome | None", "None"),
                ("stop_reason", "str | None", "None"),
                ("authenticated_review_head_sha", "str | None", "None"),
                (
                    "authenticated_review_full_diff_sha256",
                    "str | None",
                    "None",
                ),
                ("remaining_finding_ids", "tuple[str, ...]", "()"),
            ),
            "LedgerState": (
                ("repository", "str", None),
                ("pr", "int", None),
                ("reviewer", "Reviewer", None),
                ("budgets", "BudgetPolicy", None),
                ("invocations", "tuple[Invocation, ...]", "()"),
                ("consumed_override_event_ids", "tuple[int, ...]", "()"),
                (
                    "last_decision",
                    "DecisionRecord",
                    "field(default_factory=DecisionRecord)",
                ),
                ("handoff", "Handoff", "field(default_factory=Handoff)"),
            ),
        }
        if dismissals:
            # v1.63 carries the dismissal comments into the claim and the bounded
            # snapshot into the ledger; a ledger without one keeps the v1.47 bytes.
            expected_records["ClaimRequest"] += (
                ("dismiss_events", "tuple[DismissEvent, ...]", "()"),
            )
            expected_records["LedgerState"] += (
                ("dismissed_findings", "tuple[DismissedFinding, ...]", "()"),
            )
            expected_records["FinalizeRequest"] = (
                ("repository", "str", None),
                ("pr", "int", None),
                ("reviewer", "Reviewer", None),
                ("run_id", "int", None),
                ("run_attempt", "int", None),
                ("head_sha", "str", None),
                ("full_diff_sha256", "str", None),
                ("model_route", "tuple[str, ...]", None),
                ("effort", "str", None),
                ("call_count", "int", None),
                ("elapsed_seconds", "int", None),
                ("outcome", "Outcome", None),
                ("stop_reason", "str", None),
                ("authenticated_review", "AuthenticatedReview", None),
                ("remaining_finding_ids", "tuple[str, ...]", None),
                ("dismiss_events", "tuple[DismissEvent, ...]", "()"),
            )
        if any(
            _record_shape(_class_node(module, name))
            != _expected_record_shape(fields)
            for name, fields in expected_records.items()
        ):
            raise ValueError("helper record schema differs")
        schema_keys = {
            "Invocation": {
                "run_id", "run_attempt", "head_sha", "full_diff_sha256",
                "caller_workflow_path", "caller_event",
                "referenced_workflow_path", "referenced_workflow_ref",
                "referenced_workflow_sha",
                "round_number", "override_event_id", "model_route", "effort",
                "call_unit", "call_count", "estimated_input_tokens",
                "elapsed_seconds", "status", "outcome", "stop_reason",
                "remaining_finding_ids",
            },
            "Handoff": {
                "authenticated_review_full_diff_sha256",
                "authenticated_review_head_sha", "automatic_rounds",
                "current_full_diff_sha256", "current_head_sha",
                "current_run_attempt", "current_run_id", "decision", "outcome",
                "override_rounds", "pr", "remaining_finding_ids", "repository",
                "reviewer", "round_usage", "stop_reason",
            },
            "LedgerState": {
                "schema", "repository", "pr", "reviewer", "budgets",
                "invocations", "consumed_override_event_ids", "last_decision",
                "handoff",
            },
        }
        if any(
            _local_literal(_function_node(_class_node(module, name), "from_dict"), "keys")
            != keys
            for name, keys in schema_keys.items()
        ):
            raise ValueError("helper serialized schema differs")
        decision_from_dict = _function_node(
            _class_node(module, "DecisionRecord"), "from_dict"
        )
        _direct_guard(
            decision_from_dict.body,
            "decision not in {"
            "'claimed', 'finalized', 'state_invalid', 'diff_unavailable', "
            "'authenticated_reuse', 'duplicate_head', "
            "'duplicate_effective_diff', 'input_budget_exhausted', "
            "'round_budget_exhausted', 'total_usage_budget_exhausted'}",
            "BudgetStateError",
            "decision_invalid",
        )
        bounded_finding_guards = (
            (
                _function_node(_class_node(module, "Invocation"), "from_dict"),
                "not isinstance(findings, list) or len(findings) > 8 or "
                "len(findings) != len(set(findings))",
                "remaining_finding_ids_invalid",
            ),
            (
                _function_node(_class_node(module, "Handoff"), "from_dict"),
                "not isinstance(findings, list) or len(findings) > 8 or "
                "len(findings) != len(set(findings)) or not all("
                "isinstance(item, str) and _FINDING.fullmatch(item) "
                "for item in findings)",
                "handoff_invalid",
            ),
            (
                _function_node(module, "_validate_request"),
                "len(review.remaining_finding_ids) > 8 or "
                "len(review.remaining_finding_ids) != "
                "len(set(review.remaining_finding_ids)) or not all("
                "isinstance(item, str) and _FINDING.fullmatch(item) "
                "for item in review.remaining_finding_ids)",
                "authenticated_review_invalid",
            ),
            (
                _function_node(module, "_validate_finalize_request"),
                "not isinstance(findings, tuple) or len(findings) > 8 or "
                "len(findings) != len(set(findings)) or not all("
                "isinstance(item, str) and _FINDING.fullmatch(item) "
                "for item in findings)",
                "remaining_finding_ids_invalid",
            ),
        )
        for function, expression, reason in bounded_finding_guards:
            _direct_guard(
                function.body, expression, "BudgetStateError", reason
            )
        claim_request_validation = _function_node(module, "_validate_request")
        for expression, reason in (
            ("not isinstance(request.force_review, bool)", "force_review_invalid"),
            (
                "request.force_review and request.diff_mode != 'changed'",
                "force_review_diff_invalid",
            ),
        ):
            _direct_guard(
                claim_request_validation.body,
                expression,
                "BudgetStateError",
                reason,
            )
        reviewer_policy = _function_node(policy, "for_reviewer")
        if (
            len(reviewer_policy.body) != 2
            or not isinstance(reviewer_policy.body[0], ast.If)
            or not _ast_expression_matches(
                reviewer_policy.body[0].test, "reviewer not in MARKERS"
            )
            or len(reviewer_policy.body[0].body) != 1
            or not _raise_reason(
                reviewer_policy.body[0].body[0],
                "BudgetStateError",
                "reviewer_invalid",
            )
            or not _ast_statement_matches(
                reviewer_policy.body[1],
                "return cls("
                + ("max_rounds=configured_max_rounds(), " if rounds_variable else "")
                + "max_calls_per_round={'claude': 1, 'gemini': 3, "
                "'opencode': 2}[reviewer], "
                "max_wall_seconds_per_round={'claude': 1080, "
                "'gemini': 600, 'opencode': 600}[reviewer])",
            )
        ):
            raise ValueError("reviewer execution caps differ")
        finding_bindings = [
            item.value
            for item in module.body
            if isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
            and item.targets[0].id == "_FINDING"
        ]
        if (
            len(finding_bindings) != 1
            or ast.dump(finding_bindings[0], include_attributes=False)
            != ast.dump(
                ast.parse('re.compile(r"RVW-[0-9a-f]{12}\\Z")', mode="eval").body,
                include_attributes=False,
            )
        ):
            raise ValueError("finding identity differs")
        if dismissals:
            command_bindings = [
                item.value
                for item in module.body
                if isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
                and item.targets[0].id == "_DISMISS_COMMAND"
            ]
            if (
                len(command_bindings) != 1
                or ast.dump(command_bindings[0], include_attributes=False)
                != ast.dump(
                    ast.parse(
                        're.compile(r"dismiss (RVW-[0-9a-f]{12}) (\\S[^\\r\\n]*)\\Z")',
                        mode="eval",
                    ).body,
                    include_attributes=False,
                )
            ):
                raise ValueError("dismiss grammar differs")
        functions = _module_function_headers(module)
        required_functions = {
            "serialize_ledger": "def serialize_ledger(state: LedgerState) -> str",
            "parse_ledger": (
                "def parse_ledger(body: str | None, *, repository: str, pr: int, "
                "reviewer: Reviewer) -> LedgerState | None"
            ),
            "estimate_input_tokens": (
                "def estimate_input_tokens(paths: Sequence[Path]) -> int"
            ),
            "choose_override": (
                "def choose_override(state: LedgerState, "
                "events: Sequence[OverrideEvent]) -> OverrideEvent | None"
            ),
            "claim": (
                "def claim(state: LedgerState | None, request: ClaimRequest, "
                "provenances: Mapping[tuple[int, int], RunProvenance]) -> Transition"
            ),
            "finalize": (
                "def finalize(state: LedgerState, request: FinalizeRequest, "
                "provenances: Mapping[tuple[int, int], RunProvenance]) -> Transition"
            ),
            "render_checkpoint": (
                "def render_checkpoint(state: LedgerState) -> bytes"
            ),
            "load_checkpoint": (
                "def load_checkpoint(payload: bytes) -> LedgerState"
            ),
        }
        if dismissals:
            required_functions.update({
                "parse_dismiss_command": (
                    "def parse_dismiss_command(body: object) -> str | None"
                ),
                "choose_dismissals": (
                    "def choose_dismissals(state: LedgerState, "
                    "events: Sequence[DismissEvent]) -> tuple[DismissedFinding, ...]"
                ),
            })
        if any(
            functions.get(name) != header
            for name, header in required_functions.items()
        ):
            raise ValueError("public helper function differs")
        claim_function = _function_node(module, "claim")
        dismissal_snapshot = (
            "        validated = _apply_dismissals(validated, request.dismiss_events)\n"
            if dismissals
            else ""
        )
        expected_claim = ast.parse(
            "def expected(state, request, provenances):\n"
            "    try:\n"
            "        validated = validate_or_initialize(state, request, provenances)\n"
            f"{dismissal_snapshot}"
            "    except BudgetStateError as exc:\n"
            "        return _invalid_transition(state, request, str(exc))\n"
            "    same_run = [item for item in validated.invocations "
            "if (item.run_id, item.run_attempt) == "
            "(request.run_id, request.run_attempt)]\n"
            "    if same_run and any((item.head_sha, item.full_diff_sha256) != "
            "(request.head_sha, request.full_diff_sha256) "
            "for item in same_run):\n"
            "        return _invalid_transition(validated, request, "
            "'duplicate_run_identity')\n"
            "    if request.diff_mode == 'unchanged':\n"
            "        if not request.authenticated_review.covers_hash("
            "request.full_diff_sha256):\n"
            "            return _invalid_transition(validated, request, "
            "'unchanged_without_authenticated_review')\n"
            "        return refuse(validated, request, 'authenticated_reuse')\n"
            "    if not request.force_review and any("
            "item.head_sha == request.head_sha for item in validated.invocations):\n"
            "        if (request.authenticated_review.head_sha == request.head_sha "
            "and request.authenticated_review.covers_hash("
            "request.full_diff_sha256)):\n"
            "            return refuse(validated, request, 'authenticated_reuse')\n"
            "        return refuse(validated, request, 'duplicate_head')\n"
            "    if not request.force_review and any("
            "item.full_diff_sha256 == request.full_diff_sha256 "
            "for item in validated.invocations):\n"
            "        return refuse(validated, request, 'duplicate_effective_diff')\n"
            "    if request.estimated_input_tokens > "
            "validated.budgets.max_estimated_tokens_per_round:\n"
            "        return refuse(validated, request, 'input_budget_exhausted')\n"
            "    override = None\n"
            "    if any(item.override_event_id is not None "
            "for item in validated.invocations):\n"
            "        return refuse(validated, request, 'round_budget_exhausted')\n"
            "    if request.force_review:\n"
            "        override = choose_override(validated, request.override_events)\n"
            "        if override is None:\n"
            "            return refuse(validated, request, 'round_budget_exhausted')\n"
            f"    elif automatic_rounds(validated) >= {claim_rounds}:\n"
            "        override = choose_override(validated, request.override_events)\n"
            "        if override is None:\n"
            "            return refuse(validated, request, 'round_budget_exhausted')\n"
            "    total_limit = 600_000 if override is not None else 400_000\n"
            "    if estimated_total(validated) + request.estimated_input_tokens "
            "> total_limit:\n"
            "        return refuse(validated, request, "
            "'total_usage_budget_exhausted')\n"
            "    return append_claim(validated, request, override, "
            "provenances[(request.run_id, request.run_attempt)])\n"
        ).body[0]
        if ast.dump(
            ast.Module(body=claim_function.body, type_ignores=[]),
            include_attributes=False,
        ) != ast.dump(
            ast.Module(body=expected_claim.body, type_ignores=[]),
            include_attributes=False,
        ):
            raise ValueError("claim policy differs")

        finalize_function = _function_node(module, "finalize")
        if (
            len(finalize_function.body) != 17
            or not _ast_statement_matches(
                finalize_function.body[9],
                "if request.call_count > "
                "state.budgets.max_calls_per_round:\n"
                "    outcome, stop_reason = "
                "'checkpoint_failure', 'call_budget_exhausted'\n"
                "elif request.elapsed_seconds > "
                "state.budgets.max_wall_seconds_per_round:\n"
                "    outcome, stop_reason = "
                "'wall_time_exhausted', 'wall_time_exhausted'",
            )
        ):
            raise ValueError("final live cap relationship differs")

        state_shape = _function_node(module, "_validate_state_shape")
        invocation_loops = [
            statement
            for statement in state_shape.body
            if isinstance(statement, ast.For)
            and _ast_expression_matches(statement.iter, "state.invocations")
        ]
        expected_invocation_loop = ast.parse(
            "for item in state.invocations:\n"
            "    Invocation.from_dict(item.to_dict())\n"
            "    _validate_stored_provenance_identity(item, state.reviewer)\n"
            "    call_failure = (item.status == 'finalized' and "
            "item.outcome == 'checkpoint_failure' and "
            "item.stop_reason == 'call_budget_exhausted')\n"
            "    wall_failure = (item.status == 'finalized' and "
            "item.outcome == 'wall_time_exhausted' and "
            "item.stop_reason == 'wall_time_exhausted')\n"
            "    if item.call_count > state.budgets.max_calls_per_round "
            "and not call_failure:\n"
            "        raise BudgetStateError('call_budget_exhausted')\n"
            "    if item.estimated_input_tokens > "
            "state.budgets.max_estimated_tokens_per_round:\n"
            "        raise BudgetStateError('input_budget_exhausted')\n"
            "    call_first_dual_failure = call_failure and "
            "item.call_count > state.budgets.max_calls_per_round\n"
            "    if item.elapsed_seconds > "
            "state.budgets.max_wall_seconds_per_round and "
            "not (wall_failure or call_first_dual_failure):\n"
            "        raise BudgetStateError('wall_time_exhausted')"
        ).body[0]
        if (
            len(invocation_loops) != 1
            or ast.dump(invocation_loops[0], include_attributes=False)
            != ast.dump(expected_invocation_loop, include_attributes=False)
        ):
            raise ValueError("stored cap policy differs")
        expected_duplicate_policy = ast.parse(
            "automatic = [item for item in state.invocations "
            "if item.override_event_id is None]\n"
            "overrides = [item for item in state.invocations "
            "if item.override_event_id is not None]\n"
            "forced_override = next((item for item in overrides "
            "if item.caller_event == 'workflow_dispatch'), None)\n"
            "for attribute, reason in (('head_sha', 'duplicate_head'), "
            "('full_diff_sha256', 'duplicate_effective_diff')):\n"
            "    values = [getattr(item, attribute) for item in state.invocations]\n"
            "    duplicates = {value for value in values "
            "if values.count(value) > 1}\n"
            "    if duplicates and (forced_override is None or "
            "any(values.count(value) != 2 for value in duplicates) or "
            "any(getattr(forced_override, attribute) != value "
            "for value in duplicates) or "
            "all(getattr(item, attribute) not in duplicates "
            "for item in automatic)):\n"
            "        raise BudgetStateError(reason)\n"
            f"if len(automatic) > {rounds_owner}.max_rounds or "
            f"len(overrides) > {rounds_owner}.max_override_rounds:\n"
            "    raise BudgetStateError('rounds_invalid')\n"
            "if [item.round_number for item in automatic] != "
            "list(range(1, len(automatic) + 1)):\n"
            "    raise BudgetStateError('rounds_invalid')\n"
            "if overrides:\n"
            "    item = overrides[0]\n"
            "    expected_automatic_rounds = ("
            "range(0, state.budgets.max_rounds + 1) "
            "if item.caller_event == 'workflow_dispatch' "
            "else (state.budgets.max_rounds,))\n"
            "    if (len(automatic) not in expected_automatic_rounds or "
            "state.invocations[-1] != item or "
            "item.round_number != len(automatic) + 1 or "
            "item.override_event_id not in state.consumed_override_event_ids):\n"
            "        raise BudgetStateError('override_invalid')\n"
            "if len(state.consumed_override_event_ids) != len(overrides):\n"
            "    raise BudgetStateError('override_invalid')\n"
        ).body
        if ast.dump(
            ast.Module(body=state_shape.body[9 + shape_offset:17 + shape_offset], type_ignores=[]),
            include_attributes=False,
        ) != ast.dump(
            ast.Module(body=expected_duplicate_policy, type_ignores=[]),
            include_attributes=False,
        ):
            raise ValueError("forced duplicate policy differs")
        for expression, reason in (
            (
                f"len(automatic) > {rounds_owner}.max_rounds or "
                f"len(overrides) > {rounds_owner}.max_override_rounds",
                "rounds_invalid",
            ),
            (
                "automatic_total > total_limit",
                "total_usage_budget_exhausted",
            ),
            (
                "sum(item.estimated_input_tokens "
                "for item in state.invocations) > total_limit",
                "total_usage_budget_exhausted",
            ),
        ):
            _direct_guard(
                state_shape.body, expression, "BudgetStateError", reason
            )

        provenance_identity = _function_node(
            module, "_validate_provenance_identity"
        )
        _direct_guard(
            provenance_identity.body,
            "provenance.caller_event not in {'pull_request', 'workflow_dispatch'} or "
            "not isinstance(provenance.caller_workflow_path, str) or "
            "_CALLER_WORKFLOW.fullmatch(provenance.caller_workflow_path) is None or "
            "'..' in Path(provenance.caller_workflow_path).parts or "
            "not isinstance(provenance.referenced_workflow_ref, str) or "
            "_WORKFLOW_REF.fullmatch(provenance.referenced_workflow_ref) is None or "
            "provenance.referenced_workflow_path != "
            "_expected_referenced_workflow_path("
            "reviewer, provenance.referenced_workflow_sha) or "
            "not isinstance(provenance.referenced_workflow_sha, str) or "
            "_HEAD.fullmatch(provenance.referenced_workflow_sha) is None",
            "BudgetStateError",
            "provenance_mismatch",
        )
        expected_stored_provenance = ast.parse(
            "_validate_provenance_identity(\n"
            "    RunProvenance(\n"
            "        repository='stored/stored', pr=1, "
            "head_sha=invocation.head_sha,\n"
            "        caller_workflow_path=invocation.caller_workflow_path,\n"
            "        caller_event=invocation.caller_event,\n"
            "        referenced_workflow_path=invocation.referenced_workflow_path,\n"
            "        referenced_workflow_ref=invocation.referenced_workflow_ref,\n"
            "        referenced_workflow_sha=invocation.referenced_workflow_sha,\n"
            "        run_id=invocation.run_id, "
            "run_attempt=invocation.run_attempt,\n"
            "        status=invocation.status, conclusion=invocation.outcome,\n"
            "    ), reviewer,\n"
            ")"
        ).body[0]
        stored_provenance = _function_node(
            module, "_validate_stored_provenance_identity"
        )
        if (
            len(stored_provenance.body) != 1
            or ast.dump(stored_provenance.body[0], include_attributes=False)
            != ast.dump(expected_stored_provenance, include_attributes=False)
        ):
            raise ValueError("stored provenance identity differs")

        expected_one_provenance = ast.parse(
            "def expected(provenance, state, request, current, invocation):\n"
            "    _validate_provenance_identity(provenance, state.reviewer)\n"
            "    expected_head = request.head_sha if invocation is None else invocation.head_sha\n"
            "    expected_run_id = request.run_id if invocation is None else invocation.run_id\n"
            "    expected_run_attempt = (request.run_attempt if invocation is None else invocation.run_attempt)\n"
            "    expected_event = (invocation.caller_event if invocation is not None "
            "else ('workflow_dispatch' if isinstance(request, ClaimRequest) "
            "and request.force_review else 'pull_request'))\n"
            "    if (provenance.repository != state.repository or "
            "provenance.pr != state.pr or provenance.head_sha != expected_head or "
            "provenance.caller_event != expected_event or "
            "provenance.run_id != expected_run_id or "
            "provenance.run_attempt != expected_run_attempt or "
            "(not current and provenance.status != 'completed') or "
            "(current and provenance.status not in {'in_progress', 'completed'})):\n"
            "        raise BudgetStateError('provenance_mismatch')\n"
            "    if invocation is not None and ("
            "provenance.caller_workflow_path != invocation.caller_workflow_path or "
            "provenance.caller_event != invocation.caller_event or "
            "provenance.referenced_workflow_path != invocation.referenced_workflow_path or "
            "provenance.referenced_workflow_ref != invocation.referenced_workflow_ref or "
            "provenance.referenced_workflow_sha != invocation.referenced_workflow_sha):\n"
            "        raise BudgetStateError('provenance_mismatch')\n"
        ).body[0]
        one_provenance = _function_node(module, "_validate_one_provenance")
        if ast.dump(
            ast.Module(body=one_provenance.body, type_ignores=[]),
            include_attributes=False,
        ) != ast.dump(
            ast.Module(body=expected_one_provenance.body, type_ignores=[]),
            include_attributes=False,
        ):
            raise ValueError("one-run provenance policy differs")

        expected_provenance = ast.parse(
            "def expected(state, request, provenances):\n"
            "    for item in state.invocations:\n"
            "        provenance = provenances.get((item.run_id, item.run_attempt))\n"
            "        if not isinstance(provenance, RunProvenance):\n"
            "            raise BudgetStateError('provenance_mismatch')\n"
            "        current = (item.run_id, item.run_attempt) == "
            "(request.run_id, request.run_attempt)\n"
            "        _validate_one_provenance(provenance, state, request, "
            "current=current, invocation=item)\n"
            "    current_key = (request.run_id, request.run_attempt)\n"
            "    if not any((item.run_id, item.run_attempt) == current_key "
            "for item in state.invocations):\n"
            "        provenance = provenances.get(current_key)\n"
            "        if not isinstance(provenance, RunProvenance):\n"
            "            raise BudgetStateError('provenance_mismatch')\n"
            "        _validate_one_provenance(provenance, state, request, "
            "current=True, invocation=None)\n"
        ).body[0]
        provenance = _function_node(module, "_validate_provenance")
        if ast.dump(
            ast.Module(body=provenance.body, type_ignores=[]),
            include_attributes=False,
        ) != ast.dump(
            ast.Module(body=expected_provenance.body, type_ignores=[]),
            include_attributes=False,
        ):
            raise ValueError("provenance policy differs")

        run_provenances = _function_node(module, "_run_provenances")
        run_loops = [
            item for item in run_provenances.body
            if isinstance(item, ast.For)
            and _ast_expression_matches(item.iter, "sorted(identities)")
        ]
        expected_run_loop = ast.parse(
            "for run_id, run_attempt in sorted(identities):\n"
            "    path = output_directory / f'run-{run_id}-{run_attempt}.json'\n"
            "    value = _read_json(path, 'run')\n"
            "    if not isinstance(value, dict):\n"
            "        raise TransportError('provenance_mismatch')\n"
            "    repository = value.get('repository')\n"
            "    pulls = value.get('pull_requests')\n"
            "    referenced = value.get('referenced_workflows')\n"
            "    if (not isinstance(repository, dict) or "
            "not isinstance(pulls, list) or not isinstance(referenced, list)):\n"
            "        raise TransportError('provenance_mismatch')\n"
            "    pull_numbers = {item.get('number') for item in pulls "
            "if isinstance(item, dict) and isinstance(item.get('number'), int) "
            "and not isinstance(item.get('number'), bool)}\n"
            "    prefix = f\"{CENTRAL_REPOSITORY}/{WORKFLOWS[request['reviewer']]}@\"\n"
            "    central = [item for item in referenced "
            "if isinstance(item, dict) and isinstance(item.get('path'), str) "
            "and item['path'].startswith(prefix)]\n"
            "    if len(central) != 1:\n"
            "        raise TransportError('provenance_mismatch')\n"
            "    central = central[0]\n"
            "    central_ref = central.get('ref') if 'ref' in central else central.get('sha')\n"
            "    stored = stored_by_identity.get((run_id, run_attempt))\n"
            "    is_force_dispatch = (stored is not None and "
            "stored.caller_event == 'workflow_dispatch') or ("
            "stored is None and request['operation'] == 'claim' and "
            "request['force_review'] and (run_id, run_attempt) == "
            "(request['run_id'], request['run_attempt']))\n"
            "    reviewed_head = stored.head_sha if stored is not None "
            "else request['head_sha']\n"
            "    provenance = RunProvenance("
            "repository=repository.get('full_name'), pr=request['pr'], "
            "head_sha=reviewed_head if is_force_dispatch else value.get('head_sha'), "
            "caller_workflow_path=value.get('path'), "
            "caller_event=value.get('event'), "
            "referenced_workflow_path=central.get('path'), "
            "referenced_workflow_ref=central_ref, "
            "referenced_workflow_sha=central.get('sha'), "
            "run_id=value.get('id'), run_attempt=value.get('run_attempt'), "
            "status=value.get('status'), conclusion=value.get('conclusion'))\n"
            "    if is_force_dispatch:\n"
            "        if value.get('event') != 'workflow_dispatch':\n"
            "            raise TransportError('provenance_mismatch')\n"
            "    elif request['pr'] not in pull_numbers:\n"
            "        raise TransportError('provenance_mismatch')\n"
            "    _validate_provenance_identity(provenance, request['reviewer'])\n"
            "    result[(run_id, run_attempt)] = provenance\n"
        ).body[0]
        if (
            len(run_loops) != 1
            or ast.dump(run_loops[0], include_attributes=False)
            != ast.dump(expected_run_loop, include_attributes=False)
        ):
            raise ValueError("provenance transport differs")
        if not _ast_statement_matches(
            run_provenances.body[1],
            "stored_by_identity = {} if state is None else {"
            "(item.run_id, item.run_attempt): item "
            "for item in state.invocations}",
        ):
            raise ValueError("stored provenance lookup differs")
        if not _ast_statement_matches(
            run_provenances.body[2],
            "identities = {(request['run_id'], request['run_attempt'])}",
        ):
            raise ValueError("current provenance lookup differs")

        input_paths = _function_node(module, "_validated_input_paths")
        _direct_guard(
            input_paths.body,
            "request.get('operation') == 'claim' and "
            "request.get('diff_mode') in {'full', 'delta'} and not value",
            "TransportError",
            "input_files_empty",
        )
        list_identities = _function_node(module, "_list_run_identities")
        if (
            len(list_identities.body) != 8
            or not _ast_statement_matches(
                list_identities.body[4],
                "current = {'run_id': request['run_id'], "
                "'run_attempt': request['run_attempt']}",
            )
            or not _ast_statement_matches(
                list_identities.body[5],
                "if current not in runs:\n    runs.append(current)",
            )
            or not _ast_statement_matches(
                list_identities.body[6],
                "if len(runs) > 4:\n"
                "    error = 'ledger_invalid'\n"
                "    runs = []",
            )
        ):
            raise ValueError("run identity manifest differs")

        choose_override = _function_node(module, "choose_override")
        # v1.62 refuses the OpenCode override up front, which adds one guard ahead of
        # the eligibility loop.
        override_offset = 1 if rounds_variable and filter_reasons else 0
        if (
            len(choose_override.body) != 4 + override_offset
            or (
                filter_reasons
                and not _ast_statement_matches(
                    choose_override.body[0],
                    "if state.reviewer == 'opencode':\n    return None",
                )
            )
            or not isinstance(choose_override.body[2 + override_offset], ast.For)
            or not _ast_expression_matches(
                choose_override.body[2 + override_offset].iter, "events"
            )
            or len(choose_override.body[2 + override_offset].body) != 1
            or not isinstance(choose_override.body[2 + override_offset].body[0], ast.If)
            or not _ast_expression_matches(
                choose_override.body[2 + override_offset].body[0].test,
                "isinstance(event, OverrideEvent) "
                "and isinstance(event.event_id, int) "
                "and not isinstance(event.event_id, bool) "
                "and event.event_id > 0 "
                "and event.event == 'labeled' "
                "and event.label == 'review-budget-override' "
                "and event.actor_permission in {'admin', 'maintain', 'write'} "
                "and event.event_id not in "
                "state.consumed_override_event_ids",
            )
            or not _ast_statement_matches(
                choose_override.body[2 + override_offset].body[0].body[0],
                "eligible.append(event)",
            )
            or not _ast_statement_matches(
                choose_override.body[3 + override_offset],
                "return max(eligible, key=lambda item: item.event_id, "
                "default=None)",
            )
        ):
            raise ValueError("override policy differs")

        load_checkpoint = _function_node(module, "load_checkpoint")
        _direct_guard(
            load_checkpoint.body,
            "payload != render_checkpoint(state)",
            "BudgetStateError",
            "checkpoint_json_noncanonical",
        )
        cas_failed = _function_node(module, "_cas_failed")
        if (
            len(cas_failed.body) != 12
            or not _ast_statement_matches(
                cas_failed.body[6],
                "transition = _recorded_refusal("
                "prior_state, request_object, 'compare_and_swap_failed')",
            )
        ):
            raise ValueError("compare-and-swap refusal differs")
    except (StopIteration, SyntaxError, TypeError, ValueError):
        raise ReleaseVerificationError(
            "invocation-budget helper contract is invalid"
        ) from None


def require_budget_workflow_contract(
    tree: VerifiedCommitTree,
    workflow: str,
    reviewer: str,
    *,
    review_policy: bool = False,
) -> None:
    """Require reviewer semantics from authenticated commit-tree workflow bytes."""

    try:
        if REVIEWER_WORKFLOWS.get(reviewer) != workflow:
            raise ValueError("reviewer workflow mismatch")
        payload = tree.read_file(f".github/workflows/{workflow}")
        document = _load_release_yaml(
            payload,
            reject_duplicate_keys=review_policy,
        )
        if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
            raise ValueError("workflow document differs")
        jobs = document["jobs"]
        locations = {
            "claude": ("claude-review", "claude-review"),
            "gemini": ("gemini-review", "gemini-review"),
            "opencode": ("opencode-prepare", "opencode-canonicalize"),
        }
        claim_job_name, finalize_job_name = locations[reviewer]
        claim_job = jobs[claim_job_name]
        finalize_job = jobs[finalize_job_name]
        claim_steps = [
            step
            for step in claim_job["steps"]
            if step.get("uses") == REVIEW_INVOCATION_BUDGET_ACTION
            and step.get("with", {}).get("mode") == "claim"
        ]
        finalize_steps = [
            step
            for step in finalize_job["steps"]
            if step.get("uses") == REVIEW_INVOCATION_BUDGET_ACTION
            and step.get("with", {}).get("mode") == "finalize"
        ]
        if len(claim_steps) != 1 or len(finalize_steps) != 1:
            raise ValueError("claim/finalize pair differs")
        claim_step = claim_steps[0]
        finalize_step = finalize_steps[0]
        expected_claim_guards = {
            "claude": (
                "${{ always() && "
                "steps.prepare-review-input.outcome == 'success' && "
                "steps.stage-claude-budget-input.outcome == 'success' }}"
            ),
            "gemini": (
                "${{ always() && steps.pr-details.outcome == 'success' && "
                "steps.stage-gemini-budget-input.outcome == 'success' }}"
            ),
            "opencode": "${{ always() && steps.ctx.outcome == 'success' }}",
        }
        expected_finalize_guards = {
            "claude": (
                "${{ always() && !cancelled() && "
                "steps.review-budget-claim.outputs.allow-invocation == 'true' && "
                "steps.claude-budget-metrics.outputs.metrics_valid == 'true' }}"
            ),
            "gemini": (
                "${{ always() && !cancelled() && "
                "steps.review-budget-claim.outputs.allow-invocation == 'true' && "
                "steps.gemini-budget-metrics.outputs.metrics_valid == 'true' }}"
            ),
            "opencode": (
                "${{ always() && !cancelled() && "
                "needs.opencode-prepare.outputs.allow_invocation == 'true' && "
                "steps.canonicalize-opencode-review.outputs.budget_metrics_valid == 'true' }}"
            ),
        }
        if (
            claim_step.get("id") != "review-budget-claim"
            or finalize_step.get("id") != "review-budget-finalize"
            or claim_step.get("if") != expected_claim_guards[reviewer]
            or claim_step.get("with", {}).get("reviewer") != reviewer
            or finalize_step.get("with", {}).get("reviewer") != reviewer
            or claim_step.get("with", {}).get("checkpoint-file")
            != f"${{{{ runner.temp }}}}/{reviewer}-review-budget-claim.json"
            or finalize_step.get("with", {}).get("checkpoint-file")
            != f"${{{{ runner.temp }}}}/{reviewer}-review-budget-final.json"
            or finalize_step.get("if") != expected_finalize_guards[reviewer]
        ):
            raise ValueError("claim/finalize contract differs")

        if reviewer != "opencode":
            stage_name = {
                "claude": "Stage Claude budget input",
                "gemini": "Stage Gemini budget input",
            }[reviewer]
            stage = _named_step(claim_job, stage_name)
            expected_stage_id = f"stage-{reviewer}-budget-input"
            stage_source = stage.get("run", "")
            empty_output = "printf 'input_files_json=[]\\n' >> \"$GITHUB_OUTPUT\""
            source_check = '[[ -f "$source_file" && ! -L "$source_file" ]]'
            copy_command = 'cp -- "$source_file" "$staged_file"'
            compare_command = 'cmp -s -- "$source_file" "$staged_file"'
            final_output = "$(jq -cn --arg path \"$staged_file\" '[$path]')"
            if (
                stage.get("id") != expected_stage_id
                or not all(
                    fragment in stage_source
                    for fragment in (
                        empty_output, source_check, copy_command,
                        compare_command, final_output,
                    )
                )
                or not (
                    stage_source.index(empty_output)
                    < stage_source.index(source_check)
                    < stage_source.index(copy_command)
                    < stage_source.index(compare_command)
                    < stage_source.index(final_output)
                )
            ):
                raise ValueError("budget input staging differs")
            metrics_id = f"{reviewer}-budget-metrics"
            metrics_validity = (
                f"steps.{metrics_id}.outputs.metrics_valid == 'true'"
            )
            outcome_step = _named_step(
                claim_job,
                {
                    "claude": "Resolve Claude budget outcome",
                    "gemini": "Resolve Gemini budget outcome",
                }[reviewer],
            )
            if metrics_validity not in outcome_step.get("if", ""):
                raise ValueError("budget outcome metrics guard differs")
            expected_finalize_metrics = {
                "model-route-json": (
                    f"${{{{ steps.{metrics_id}.outputs.model_route_json }}}}"
                ),
                "actual-call-count": (
                    f"${{{{ steps.{metrics_id}.outputs.call_count }}}}"
                ),
                "elapsed-seconds": (
                    f"${{{{ steps.{metrics_id}.outputs.elapsed_seconds }}}}"
                ),
            }
            if any(
                finalize_step.get("with", {}).get(name) != value
                for name, value in expected_finalize_metrics.items()
            ):
                raise ValueError("budget finalized metrics differ")

        provider_names = {
            "claude": "Run Claude Code Review",
            "gemini": "Run Gemini Code Review",
            "opencode": "Run OpenCode PR review",
        }
        provider_job = jobs[
            claim_job_name if reviewer != "opencode" else "opencode-review"
        ]
        provider = _named_step(provider_job, provider_names[reviewer])
        expected_provider_guards = {
            "claude": (
                "${{ steps.prepare-diff.outputs.diff-ready == 'true' && "
                "steps.prepare-diff.outputs.diff-mode != 'unchanged' && "
                "steps.review-budget-claim.outputs.allow-invocation == 'true' }}"
            ),
            "gemini": (
                "${{ steps.reset-gemini-artifacts.outcome == 'success' && "
                "steps.prepare-diff.outputs.diff-ready == 'true' && "
                "steps.prepare-diff.outputs.diff-mode != 'unchanged' && "
                "steps.review-budget-claim.outputs.allow-invocation == 'true' }}"
            ),
            "opencode": (
                "needs.opencode-prepare.outputs.allow_invocation == 'true' && "
                "needs.opencode-prepare.outputs.diff_ready == 'true' && "
                "needs.opencode-prepare.outputs.diff_mode != 'unchanged'"
            ),
        }
        if provider.get("if") != expected_provider_guards[reviewer]:
            raise ValueError("provider allow predicate differs")
        expected_timeouts = {"claude": "20", "gemini": "10", "opencode": "10"}
        if provider_job.get("timeout-minutes") != expected_timeouts[reviewer]:
            raise ValueError("review timeout differs")

        claim_names = {
            "claude": "Upload Claude review budget claim checkpoint",
            "gemini": "Upload Gemini review budget claim checkpoint",
            "opencode": "Upload OpenCode review budget claim checkpoint",
        }
        final_names = {
            "claude": "Upload Claude review budget final checkpoint",
            "gemini": "Upload Gemini review budget final checkpoint",
            "opencode": "Upload OpenCode review budget final checkpoint",
        }
        claim_upload = _named_step(claim_job, claim_names[reviewer])
        final_upload = _named_step(finalize_job, final_names[reviewer])
        expected_claim_if = (
            "${{ always() && steps.review-budget-claim.outcome == 'success' }}"
            if reviewer == "opencode"
            else "${{ always() && !cancelled() }}"
        )
        expected_claim_missing = "error" if reviewer == "opencode" else "ignore"
        expected_claim_upload = {
            "name": claim_names[reviewer],
            "if": expected_claim_if,
            "uses": UPLOAD_ARTIFACT_ACTION,
            "with": {
                "name": (
                    f"{reviewer}-review-budget-claim-"
                    "${{ github.run_id }}-${{ github.run_attempt }}"
                ),
                "path": f"${{{{ runner.temp }}}}/{reviewer}-review-budget-claim.json",
                "if-no-files-found": expected_claim_missing,
                "retention-days": "7",
                "overwrite": "false",
            },
        }
        expected_final_upload = {
            "name": final_names[reviewer],
            "if": (
                "${{ always() && !cancelled() && "
                "steps.review-budget-finalize.outcome != 'skipped' }}"
            ),
            "uses": UPLOAD_ARTIFACT_ACTION,
            "with": {
                "name": (
                    f"{reviewer}-review-budget-final-"
                    "${{ github.run_id }}-${{ github.run_attempt }}"
                ),
                "path": f"${{{{ runner.temp }}}}/{reviewer}-review-budget-final.json",
                "if-no-files-found": "ignore",
                "retention-days": "7",
                "overwrite": "false",
            },
        }
        if claim_upload != expected_claim_upload or final_upload != expected_final_upload:
            raise ValueError("budget checkpoint artifact differs")

        claim_index = claim_job["steps"].index(claim_step)
        claim_upload_index = claim_job["steps"].index(claim_upload)
        final_index = finalize_job["steps"].index(finalize_step)
        final_upload_index = finalize_job["steps"].index(final_upload)
        if not final_index < final_upload_index:
            raise ValueError("final checkpoint publication order differs")
        if reviewer != "opencode":
            canonical = _named_step(
                claim_job,
                {
                    "claude": "Canonicalize Claude review",
                    "gemini": "Canonicalize Gemini review",
                }[reviewer],
            )
            upsert = _named_step(claim_job, "Upsert review comment")
            if not (
                claim_index
                < claim_upload_index
                < claim_job["steps"].index(provider)
                < claim_job["steps"].index(canonical)
                < claim_job["steps"].index(upsert)
                < final_index
                < final_upload_index
            ):
                raise ValueError("review publication order differs")
        else:
            build_handoff = _named_step(
                claim_job, "Build sealed canonicalization handoff"
            )
            upload_handoff = _named_step(
                claim_job, "Upload sealed canonicalization handoff"
            )
            canonical = _named_step(
                finalize_job, "Canonicalize OpenCode review"
            )
            if not (
                claim_index
                < claim_job["steps"].index(build_handoff)
                < claim_job["steps"].index(upload_handoff)
                < claim_upload_index
            ):
                raise ValueError("OpenCode claim handoff order differs")
            if not (
                finalize_job["steps"].index(canonical)
                < final_index
                < final_upload_index
            ):
                raise ValueError("OpenCode publication order differs")
            if provider_job.get("needs") != ["check-enabled", "opencode-prepare"]:
                raise ValueError("OpenCode provider dependencies differ")
            provider_job_if = " ".join(provider_job.get("if", "").split())
            expected_job_if = "needs.check-enabled.outputs.enabled == 'true' && "
            expected_job_if += (
                "needs.check-enabled.outputs.policy_run == 'true' && "
                if review_policy
                else (
                    "needs.check-enabled.outputs.auto_enabled == 'true' && "
                    "needs.check-enabled.outputs.safe_pr == 'true' && "
                )
            )
            expected_job_if += (
                "needs.opencode-prepare.result == 'success' && "
                "needs.opencode-prepare.outputs.allow_invocation == 'true'"
            )
            if provider_job_if != expected_job_if:
                raise ValueError("OpenCode cross-job allow predicate differs")
            if claim_job.get("outputs", {}).get("allow_invocation") != (
                "${{ steps.review-budget-claim.outputs.allow-invocation }}"
            ):
                raise ValueError("OpenCode claim output differs")
            build_env = build_handoff.get("env", {})
            if {
                "BUDGET_CHECKPOINT_PATH": build_env.get("BUDGET_CHECKPOINT_PATH"),
                "BUDGET_CHECKPOINT_SHA256": build_env.get("BUDGET_CHECKPOINT_SHA256"),
                "BUDGET_DECISION": build_env.get("BUDGET_DECISION"),
                "ALLOW_INVOCATION": build_env.get("ALLOW_INVOCATION"),
            } != {
                "BUDGET_CHECKPOINT_PATH": (
                    "${{ runner.temp }}/opencode-review-budget-claim.json"
                ),
                "BUDGET_CHECKPOINT_SHA256": (
                    "${{ steps.review-budget-claim.outputs.checkpoint-sha256 }}"
                ),
                "BUDGET_DECISION": (
                    "${{ steps.review-budget-claim.outputs.decision }}"
                ),
                "ALLOW_INVOCATION": (
                    "${{ steps.review-budget-claim.outputs.allow-invocation }}"
                ),
            }:
                raise ValueError("OpenCode sealed handoff inputs differ")
            build_source = build_handoff.get("run", "")
            canonical_source = canonical.get("with", {}).get("script", "")
            required_handoff = (
                'cp -- "$BUDGET_CHECKPOINT_PATH" "$handoff/review-budget-claim.json"',
                '[[ "$budget_checkpoint" == "$BUDGET_CHECKPOINT_SHA256" ]]',
                '"review-budget-claim.json":$budget_checkpoint',
                "budget_checkpoint_sha256:$budget_checkpoint_sha256",
            )
            required_validation = (
                "handoff.budget_checkpoint_sha256 !== process.env.BUDGET_CHECKPOINT_SHA256",
                "handoff.files['review-budget-claim.json'] !== handoff.budget_checkpoint_sha256",
                "validated_call_count",
                "validated_elapsed_seconds",
                "validated_model_route_json",
            )
            if (
                not all(fragment in build_source for fragment in required_handoff)
                or not all(
                    fragment in canonical_source for fragment in required_validation
                )
                or finalize_step.get("with", {}).get("actual-call-count")
                != "${{ steps.canonicalize-opencode-review.outputs.validated_call_count }}"
                or finalize_step.get("with", {}).get("elapsed-seconds")
                != "${{ steps.canonicalize-opencode-review.outputs.validated_elapsed_seconds }}"
            ):
                raise ValueError("OpenCode sealed handoff validation differs")

        if reviewer == "claude":
            metrics_start = _named_step(
                claim_job, "Start Claude review metrics"
            )
            if metrics_start != {
                "name": "Start Claude review metrics",
                "id": "claude-budget-metrics-start",
                "if": (
                    "${{ steps.review-budget-claim.outputs."
                    "allow-invocation == 'true' }}"
                ),
                "shell": "bash",
                "run": (
                    "set -euo pipefail\n"
                    "printf 'call_count=1\\n' >> \"$GITHUB_OUTPUT\"\n"
                    "printf 'started_at=%s\\n' \"$(date +%s)\" "
                    ">> \"$GITHUB_OUTPUT\"\n"
                ),
            }:
                raise ValueError("Claude live call accounting differs")
            elapsed = _named_step(claim_job, "Capture Claude elapsed time")
            if elapsed != {
                "name": "Capture Claude elapsed time",
                "id": "claude-budget-elapsed",
                "if": (
                    "${{ always() && steps.claude-budget-metrics-start."
                    "outcome == 'success' }}"
                ),
                "shell": "bash",
                "env": {
                    "STARTED_AT": (
                        "${{ steps.claude-budget-metrics-start.outputs.started_at }}"
                    ),
                },
                "run": (
                    "set -euo pipefail\n"
                    "now=\"$(date +%s)\"\n"
                    "[[ \"$STARTED_AT\" =~ ^[0-9]+$ ]]\n"
                    "(( now >= STARTED_AT ))\n"
                    "printf 'elapsed_seconds=%s\\n' \"$((now - STARTED_AT))\" "
                    ">> \"$GITHUB_OUTPUT\"\n"
                ),
            }:
                raise ValueError("Claude elapsed accounting differs")
            metrics_step = _named_step(
                claim_job, "Validate Claude review metrics"
            )
            if metrics_step != {
                "name": "Validate Claude review metrics",
                "id": "claude-budget-metrics",
                "if": (
                    "${{ always() && steps.review-budget-claim.outputs."
                    "allow-invocation == 'true' }}"
                ),
                "shell": "bash",
                "env": {
                    "CALL_COUNT": (
                        "${{ steps.claude-budget-metrics-start.outputs.call_count }}"
                    ),
                    "ELAPSED_SECONDS": (
                        "${{ steps.claude-budget-elapsed.outputs.elapsed_seconds }}"
                    ),
                    "MODEL_ROUTE_JSON": (
                        "${{ steps.claude-budget-config.outputs.model_route_json }}"
                    ),
                },
                "run": (
                    "set -euo pipefail\n"
                    "printf 'metrics_valid=false\\n' >> \"$GITHUB_OUTPUT\"\n"
                    "if [[ ! \"$CALL_COUNT\" =~ ^[0-9]+$ ]] || "
                    "(( CALL_COUNT > 1 )); then\n"
                    "  exit 0\n"
                    "fi\n"
                    "if [[ ! \"$ELAPSED_SECONDS\" =~ ^[0-9]+$ ]]; then\n"
                    "  exit 0\n"
                    "fi\n"
                    "if ! jq -e '\n"
                    "    type == \"array\" and length == 1\n"
                    "    and all(.[]; type == \"string\" and length > 0)\n"
                    "  ' >/dev/null <<< \"$MODEL_ROUTE_JSON\"; then\n"
                    "  exit 0\n"
                    "fi\n"
                    "{\n"
                    "  printf 'call_count=%s\\n' \"$CALL_COUNT\"\n"
                    "  printf 'elapsed_seconds=%s\\n' \"$ELAPSED_SECONDS\"\n"
                    "  printf 'model_route_json=%s\\n' "
                    "\"$(jq -c . <<< \"$MODEL_ROUTE_JSON\")\"\n"
                    "  printf 'metrics_valid=true\\n'\n"
                    "} >> \"$GITHUB_OUTPUT\"\n"
                ),
            }:
                raise ValueError("Claude trusted metric publication differs")
        elif reviewer == "gemini":
            if provider.get("env", {}).get("GEMINI_CALL_COUNT_FILE") != (
                "${{ runner.temp }}/gemini_call_count.txt"
            ):
                raise ValueError("Gemini counter input differs")
            embedded = _heredoc_python(
                provider.get("run", ""),
                "cat > gemini_review.py << 'PYTHON_EOF'",
                "PYTHON_EOF",
            )
            counted = _function_node(embedded, "counted_generate_content")
            expected_counted = ast.parse(
                "def counted_generate_content(prompt, model):\n"
                "    count = read_call_count()\n"
                "    if count >= 3:\n"
                "        raise ProviderFailure('call_budget_exhausted')\n"
                "    write_call_count(count + 1)\n"
                "    append_model_route(model)\n"
                "    return generate_content(prompt, model)\n"
            ).body[0]
            if ast.dump(counted, include_attributes=False) != ast.dump(
                expected_counted, include_attributes=False
            ):
                raise ValueError("Gemini live call accounting differs")
            metrics_step = _named_step(
                claim_job, "Read Gemini review metrics"
            )
            if (
                metrics_step.get("id") != "gemini-budget-metrics"
                or metrics_step.get("if") != (
                    "${{ always() && steps.review-budget-claim.outputs."
                    "allow-invocation == 'true' }}"
                )
                or metrics_step.get("env") != {
                    "CALL_COUNT_FILE": (
                        "${{ runner.temp }}/gemini_call_count.txt"
                    ),
                    "STARTED_AT_FILE": (
                        "${{ runner.temp }}/gemini_started_at.txt"
                    ),
                    "ELAPSED_SECONDS_FILE": (
                        "${{ runner.temp }}/gemini_elapsed_seconds.txt"
                    ),
                    "MODEL_ROUTE_FILE": (
                        "${{ runner.temp }}/gemini_model_route.json"
                    ),
                    "CONFIGURED_MODEL_ROUTE_JSON": (
                        "${{ steps.gemini-budget-config.outputs.model_route_json }}"
                    ),
                }
            ):
                raise ValueError("Gemini metric inputs differ")
            metrics_source = metrics_step.get("run", "")
            required_metrics = (
                "output.write('metrics_valid=false\\n')",
                "valid = call_count is not None and started_at is not None "
                "and elapsed_seconds is not None",
                "if not isinstance(configured, list) or len(configured) != 1",
                "if not isinstance(route, list) or not all(",
                "if not valid:\n    raise SystemExit(0)",
                "if call_count == 0 and route == []:\n"
                "    effective_route = configured",
                "elif call_count > 0 and route and route[0] == configured[0]:\n"
                "    effective_route = route",
                "output.write(f\"call_count={call_count}\\n\")",
                "output.write(f\"elapsed_seconds={elapsed_seconds}\\n\")",
                "output.write(f\"metrics_valid={'true' if valid else 'false'}\\n\")",
            )
            if (
                not all(fragment in metrics_source for fragment in required_metrics)
                or metrics_source.index(required_metrics[0])
                >= metrics_source.index(required_metrics[4])
                or metrics_source.index(required_metrics[4])
                >= metrics_source.index(required_metrics[7])
            ):
                raise ValueError("Gemini trusted metric publication differs")
        else:
            cap = "(( count < 2 )) || {"
            cap_control = (f"group:{cap}",)
            expected_commands = (
                _ShellCommand('local prompt_path="$1"', ()),
                _ShellCommand('local output_path="$2"', ()),
                _ShellCommand("shift 2", ()),
                _ShellCommand("local count", ()),
                _ShellCommand(
                    '[[ -f "$call_count_file" && ! -L "$call_count_file" ]]',
                    (),
                ),
                _ShellCommand(
                    '[[ "$(stat -c \'%a\' "$call_count_file")" == 600 ]]',
                    (),
                ),
                _ShellCommand('count="$(cat "$call_count_file")"', ()),
                _ShellCommand('[[ "$count" =~ ^[0-9]+$ ]]', ()),
                _ShellCommand(cap, ()),
                _ShellCommand(
                    "review_failure_reason=call_budget_exhausted",
                    cap_control,
                ),
                _ShellCommand("return 1", cap_control),
                _ShellCommand(
                    'python3 - "$call_count_file" "$((count + 1))" <<\'PY\'',
                    (),
                ),
                _ShellCommand(
                    'env -i PATH="$PATH" HOME="$isolated_home" '
                    'XDG_CONFIG_HOME="$isolated_xdg" '
                    'XDG_DATA_HOME="$isolated_xdg/data" '
                    'XDG_CACHE_HOME="$isolated_xdg/cache" '
                    'ZHIPU_API_KEY="$ZHIPU_API_KEY" '
                    'OPENCODE_PURE="$OPENCODE_PURE" '
                    'OPENCODE_DISABLE_PROJECT_CONFIG='
                    '"$OPENCODE_DISABLE_PROJECT_CONFIG" '
                    'OPENCODE_CONFIG_CONTENT="$OPENCODE_CONFIG_CONTENT" '
                    'opencode run --model zai-coding-plan/glm-4.7 '
                    '--format json "$@" < "$prompt_path" > "$output_path"',
                    (),
                ),
            )
            expected_invocations = (
                _ShellCommand(
                    'run_opencode "$initial_prompt" '
                    '"$RUNNER_TEMP/opencode-review.jsonl" '
                    "--file review-full.diff --file review-scope.json",
                    (),
                ),
                _ShellCommand(
                    'run_opencode "$repair_prompt" '
                    '"$RUNNER_TEMP/opencode-format-repair.jsonl"',
                    (
                            "if:if ! candidate_outer_format_valid "
                            '"$candidate_dir/review.md" initial; then',
                    ),
                ),
            )
            function = _shell_function_analysis(
                provider.get("run", ""), "run_opencode"
            )
            if (
                function.commands != expected_commands
                or function.invocations != expected_invocations
            ):
                raise ValueError("OpenCode live call accounting differs")
        forbidden_reviewers = {
            "claude": ("Fallback to Gemini reviewer", "Fallback to OpenCode reviewer"),
            "gemini": ("Fallback to Claude reviewer", "Fallback to OpenCode reviewer"),
            "opencode": ("Fallback to Claude reviewer", "Fallback to Gemini reviewer"),
        }[reviewer]
        step_names = {
            step.get("name")
            for job in jobs.values()
            for step in job.get("steps", [])
            if isinstance(step, dict)
        }
        if any(name in step_names for name in forbidden_reviewers):
            raise ValueError("cross-reviewer fallback")
    except (
        KeyError,
        ReleaseVerificationError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        yaml.YAMLError,
    ):
        raise ReleaseVerificationError(
            "invocation-budget workflow contract is invalid"
        ) from None

def _verify_review_invocation_budget(
    tree: VerifiedCommitTree, ref: str
) -> None:
    if not release_supports_review_invocation_budget(ref):
        return
    expected_files = {
        (REVIEW_INVOCATION_BUDGET_ACTION_ROOT.path.as_posix(), "100644", "blob"),
        (REVIEW_INVOCATION_BUDGET_HELPER_ROOT.path.as_posix(), "100644", "blob"),
    }
    actual_files = {
        (entry.path.as_posix(), entry.mode, entry.object_type)
        for entry in tree.files(REVIEW_INVOCATION_BUDGET_ACTION_ROOT.path.parent)
    }
    if actual_files != expected_files:
        raise ReleaseVerificationError(
            "invocation-budget inventory is not closed"
        )
    action_path = REVIEW_INVOCATION_BUDGET_ACTION_ROOT.path.as_posix()
    try:
        action_payload = tree.read_file(action_path)
        action = _load_release_yaml(
            action_payload,
            reject_duplicate_keys=release_supports_review_policy(ref),
        )
        expected_action = (
            EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_V163
            if release_supports_finding_dismissal(ref)
            else EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_V160
            if release_supports_review_rounds_variable(ref)
            else EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION
        )
        if action != expected_action:
            raise ValueError("action differs")
    except (AttributeError, ReleaseVerificationError, TypeError, ValueError, yaml.YAMLError):
        raise ReleaseVerificationError(
            "invocation-budget action contract is invalid"
        ) from None
    helper_payload = tree.read_file(REVIEW_INVOCATION_BUDGET_HELPER_ROOT.path)
    try:
        helper = helper_payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ReleaseVerificationError(
            "invocation-budget helper contract is invalid"
        ) from None
    require_budget_helper_contract(
        helper,
        release_supports_review_rounds_variable(ref),
        release_supports_filter_reason_surface(ref),
        release_supports_finding_dismissal(ref),
    )
    for reviewer, workflow in REVIEWER_WORKFLOWS.items():
        require_budget_workflow_contract(
            tree,
            workflow,
            reviewer,
            review_policy=release_supports_review_policy(ref),
        )
    rounds_variable = release_supports_review_rounds_variable(ref)
    dismissals = release_supports_finding_dismissal(ref)
    authenticated_digests = {
        action_path: (
            action_payload,
            EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_SHA256_V163
            if dismissals
            else EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_SHA256_V160
            if rounds_variable
            else EXPECTED_REVIEW_INVOCATION_BUDGET_ACTION_SHA256,
        ),
        REVIEW_INVOCATION_BUDGET_HELPER_ROOT.path.as_posix(): (
            helper_payload,
            EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V171
            if release_supports_opencode_dismissals(ref)
            else EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V163
            if dismissals
            else EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V162
            if release_supports_filter_reason_surface(ref)
            else EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256_V160
            if rounds_variable
            else EXPECTED_REVIEW_INVOCATION_BUDGET_HELPER_SHA256,
        ),
    }
    workflow_digests = (
        EXPECTED_OPENCODE_DISMISSAL_WORKFLOW_SHA256
        if release_supports_opencode_dismissals(ref)
        else EXPECTED_OPENCODE_FINDING_ID_WORKFLOW_SHA256
        if release_supports_opencode_finding_ids(ref)
        else EXPECTED_LABEL_MISMATCH_WORKFLOW_SHA256
        if release_supports_label_mismatch_decline(ref)
        else EXPECTED_SKIP_REASON_WORKFLOW_SHA256
        if release_supports_skip_reason_notice(ref)
        else EXPECTED_FINDING_DISMISSAL_WORKFLOW_SHA256
        if dismissals
        else EXPECTED_FILTER_REASON_WORKFLOW_SHA256
        if release_supports_filter_reason_surface(ref)
        else EXPECTED_SAME_HEAD_CANCEL_WORKFLOW_SHA256
        if release_supports_same_head_cancel_guard(ref)
        else EXPECTED_REVIEW_ROUNDS_VARIABLE_WORKFLOW_SHA256
        if rounds_variable
        else EXPECTED_REVIEW_POLICY_WORKFLOW_SHA256
        if release_supports_review_policy(ref)
        else EXPECTED_REVIEW_INVOCATION_BUDGET_WORKFLOW_SHA256
    )
    for reviewer, workflow in REVIEWER_WORKFLOWS.items():
        path = f".github/workflows/{workflow}"
        authenticated_digests[path] = (
            tree.read_file(path),
            workflow_digests[reviewer],
        )
    if any(
        hashlib.sha256(payload).hexdigest() != expected
        for payload, expected in authenticated_digests.values()
    ):
        raise ReleaseVerificationError(
            "invocation-budget authenticated source digest differs"
        )


def expected_review_actions(ref: str, workflow: str) -> list[str]:
    """Return the exact ordered release-local action dependencies."""

    actions: list[str] = []
    if release_supports_review_policy(ref):
        actions.append(REVIEW_POLICY_ACTION)
    if _release_version(ref) >= (1, 46) and workflow == "gemini-auto-review.yml":
        actions.append(SETUP_GEMINI_AUTH_REVIEW)
    if release_supports_prepare_review_diff(ref):
        actions.append(PREPARE_REVIEW_DIFF_ACTION)
    if release_supports_review_invocation_budget(ref):
        actions.append(REVIEW_INVOCATION_BUDGET_ACTION)
    if (
        release_supports_canonicalize_review(ref)
        and workflow in CANONICALIZE_REVIEW_WORKFLOWS
    ):
        actions.append(CANONICALIZE_REVIEW_ACTION)
    if release_supports_review_invocation_budget(ref):
        actions.append(REVIEW_INVOCATION_BUDGET_ACTION)
    return actions


def _normalize_expression(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expression is not a string")
    return " ".join(value.split())


def _verify_review_policy_workflows(documents: dict[str, dict]) -> None:
    contracts = {
        "claude-code-review.yml": {
            "workflow_name": "claude-code-review",
            "provider_job": "claude-review",
            "pr_number": "${{ inputs.pr_number || github.event.pull_request.number }}",
            "outputs": {
                "enabled": "${{ steps.check.outputs.enabled }}",
                "policy_run": "${{ steps.review_policy.outputs.run-review }}",
                "policy_reason": "${{ steps.review_policy.outputs.reason }}",
                "policy_head": "${{ steps.review_policy.outputs.head-sha }}",
                "model": "${{ steps.model.outputs.model }}",
            },
        },
        "gemini-auto-review.yml": {
            "workflow_name": "gemini-auto-review",
            "provider_job": "gemini-review",
            "pr_number": "${{ inputs.pr_number || github.event.pull_request.number }}",
            "outputs": {
                "enabled": "${{ steps.check.outputs.enabled }}",
                "policy_run": "${{ steps.review_policy.outputs.run-review }}",
                "policy_reason": "${{ steps.review_policy.outputs.reason }}",
                "policy_head": "${{ steps.review_policy.outputs.head-sha }}",
            },
        },
        "opencode-auto-review.yml": {
            "workflow_name": "opencode-auto-review",
            "provider_job": "opencode-review",
            "pr_number": (
                "${{ inputs.pr_number || github.event.pull_request.number || "
                "github.event.issue.number }}"
            ),
            "outputs": {
                "enabled": "${{ steps.check.outputs.enabled }}",
                "policy_run": "${{ steps.review_policy.outputs.run-review }}",
                "policy_reason": "${{ steps.review_policy.outputs.reason }}",
                "policy_head": "${{ steps.review_policy.outputs.head-sha }}",
            },
        },
    }
    review_mode_input = {
        "description": "Resolved PR review policy",
        "type": "string",
        "required": "false",
        "default": "auto",
    }
    try:
        for workflow, contract in contracts.items():
            document = documents[workflow]
            jobs = document["jobs"]
            call_inputs = document["on"]["workflow_call"]["inputs"]
            if call_inputs.get("review_mode") != review_mode_input:
                raise ValueError("review mode input differs")
            check = jobs["check-enabled"]
            if check.get("outputs") != contract["outputs"]:
                raise ValueError("policy outputs differ")
            policy_steps = [
                step
                for step in check["steps"]
                if step.get("uses") == REVIEW_POLICY_ACTION
            ]
            expected_step = {
                "name": "Resolve PR review policy",
                "id": "review_policy",
                "uses": REVIEW_POLICY_ACTION,
                "with": {
                    "workflow-name": contract["workflow_name"],
                    "pr-number": contract["pr_number"],
                    "review-mode": "${{ inputs.review_mode }}",
                    "force-run": "${{ inputs.force_run && 'true' || 'false' }}",
                    "force-review": (
                        "${{ inputs.force_review && 'true' || 'false' }}"
                    ),
                    "github-token": "${{ github.token }}",
                },
            }
            if policy_steps != [expected_step]:
                raise ValueError("policy call differs")
            provider_job = jobs[contract["provider_job"]]
            provider_if = _normalize_expression(provider_job.get("if"))
            if workflow == "opencode-auto-review.yml":
                expected_provider_if = (
                    "needs.check-enabled.outputs.enabled == 'true' && "
                    "needs.check-enabled.outputs.policy_run == 'true' && "
                    "needs.opencode-prepare.result == 'success' && "
                    "needs.opencode-prepare.outputs.allow_invocation == 'true'"
                )
                if provider_job.get("needs") != [
                    "check-enabled",
                    "opencode-prepare",
                ]:
                    raise ValueError("provider needs differ")
                for job_name in ("opencode-prepare", "opencode-canonicalize"):
                    if "needs.check-enabled.outputs.policy_run == 'true'" not in (
                        _normalize_expression(jobs[job_name].get("if"))
                    ):
                        raise ValueError("pipeline policy predicate differs")
            else:
                expected_provider_if = (
                    "needs.check-enabled.outputs.enabled == 'true' && "
                    "needs.check-enabled.outputs.policy_run == 'true'"
                )
                if provider_job.get("needs") != "check-enabled":
                    raise ValueError("provider needs differ")
            if provider_if != expected_provider_if:
                raise ValueError("provider policy predicate differs")
            skipped = jobs["skipped"]
            skipped_if = _normalize_expression(skipped.get("if"))
            if "needs.check-enabled.outputs.policy_run != 'true'" not in skipped_if:
                raise ValueError("skipped policy predicate differs")
    except (KeyError, TypeError, ValueError):
        raise ReleaseVerificationError(
            "review-policy workflow contract is invalid"
        ) from None


def _verify_review_policy_callers(tree: VerifiedCommitTree, ref: str) -> None:
    # v1.64 subscribes the callers to `labeled`, guards that event on the
    # `review:request` label so unrelated labels start no run, and describes
    # `force_review` as the override round that needs `review-budget-override`.
    label_trigger = release_supports_label_review_trigger(ref)
    trigger = {
        "pull_request": {
            "types": (
                ["opened", "synchronize", "ready_for_review", "labeled"]
                if label_trigger
                else ["opened", "synchronize", "ready_for_review"]
            )
        },
        "workflow_dispatch": {
            "inputs": {
                "pr_number": {
                    "description": "Pull request number",
                    "type": "number",
                    "required": "true",
                },
                "force_review": {
                    "description": (
                        "Perform one authorized same-HEAD override round; "
                        "requires the review-budget-override label"
                        if label_trigger
                        else "Perform one authorized same-HEAD review"
                    ),
                    "type": "boolean",
                    "required": "false",
                    "default": "false",
                },
            }
        },
    }
    caller_if = (
        "(github.event_name == 'pull_request' && "
        "github.event.pull_request.head.repo.fork == false && "
        "github.event.pull_request.head.repo.full_name == github.repository && "
        + (
            "github.event.pull_request.draft == false && "
            "(github.event.action != 'labeled' || "
            "github.event.label.name == 'review:request')) || "
            if label_trigger
            else "github.event.pull_request.draft == false) || "
        )
        + "(github.event_name == 'workflow_dispatch' && inputs.force_review)"
    )
    review_mode = (
        "${{\n"
        "  github.event_name == 'workflow_dispatch' && inputs.force_review && 'request' ||\n"
        "  contains(github.event.pull_request.labels.*.name, 'review:request') &&\n"
        "  contains(github.event.pull_request.labels.*.name, 'review:skip') && 'conflict' ||\n"
        "  contains(github.event.pull_request.labels.*.name, 'review:request') && 'request' ||\n"
        "  contains(github.event.pull_request.labels.*.name, 'review:skip') && 'skip' ||\n"
        "  'auto'\n"
        "}}"
    )
    callers = {
        "claude-code-review.yml": "claude-review",
        "gemini-auto-review.yml": "gemini-review",
        "opencode-auto-review.yml": "opencode-review",
    }
    try:
        for workflow, job_name in callers.items():
            path = (
                "examples/baseline-workflows/.github/workflows/" + workflow
            )
            document = _load_release_yaml(
                tree.read_file(path),
                reject_duplicate_keys=True,
            )
            if not isinstance(document, dict) or document.get("on") != trigger:
                raise ValueError("caller trigger differs")
            job = document["jobs"][job_name]
            if _normalize_expression(job.get("if")) != caller_if:
                raise ValueError("caller draft/manual guard differs")
            values = job["with"]
            if (
                values.get("pr_number")
                != "${{ github.event.pull_request.number || inputs.pr_number }}"
                or values.get("force_review")
                != "${{ github.event_name == 'workflow_dispatch' && inputs.force_review }}"
                or values.get("review_mode") != review_mode
            ):
                raise ValueError("caller review mode differs")
    except (
        KeyError,
        ReleaseVerificationError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ):
        raise ReleaseVerificationError(
            "review-policy caller contract is invalid"
        ) from None


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


def _expected_canonicalize_step(
    contract: dict[str, str], *, budget: bool = False, dismissals: bool = False
) -> dict[str, object]:
    reviewer = contract["reviewer"]
    reset = contract["reset"]
    return {
        "name": contract["canonical_step"],
        "id": "canonicalize-review",
        "if": (
            f"${{{{ always() && steps.{reset}.outcome == 'success' && "
            "steps.prepare-diff.outputs.diff-ready == 'true' && "
            "steps.prepare-diff.outputs.diff-mode != 'unchanged'"
            + (
                " && steps.review-budget-claim.outputs.allow-invocation == 'true' }}"
                if budget
                else " }}"
            )
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
            # v1.63 hands the budget claim's dismissed finding IDs to the canonicalizer.
            **(
                {
                    "dismissed-finding-ids": (
                        "${{ steps.review-budget-claim.outputs.dismissed-finding-ids }}"
                    ),
                }
                if dismissals
                else {}
            ),
        },
    }


def _expected_prepared_head_checkout_step(*, budget: bool = False) -> dict[str, object]:
    return {
        "name": "Checkout prepared review head",
        "if": (
            "${{ steps.prepare-diff.outputs.diff-ready == 'true' && "
            "steps.prepare-diff.outputs.diff-mode != 'unchanged' && "
            "steps.review-budget-claim.outputs.allow-invocation == 'true' }}"
            if budget
            else "${{ steps.prepare-diff.outputs.diff-ready == 'true' }}"
        ),
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
    *,
    budget: bool = False,
) -> dict[str, object]:
    reviewer = contract["reviewer"]
    return {
        "name": f"Upload rejected {reviewer.capitalize()} review diagnostic",
        "if": (
            "${{ always() && "
            + (
                "steps.review-budget-claim.outputs.allow-invocation == 'true' && "
                if budget
                else ""
            )
            + "steps.canonicalize-review.outcome != 'skipped' "
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


def _expected_review_candidate_upload_step(
    contract: dict[str, str],
) -> dict[str, object]:
    reviewer = contract["reviewer"]
    return {
        "name": f"Upload {reviewer.capitalize()} review candidate",
        "id": "upload-candidate",
        "if": (
            "${{ always() "
            "&& steps.review-budget-claim.outputs.allow-invocation == 'true' "
            "&& steps.canonicalize-review.outcome != 'skipped' "
            "&& steps.canonicalize-review.outputs.document-valid != 'true' "
            f"&& hashFiles('{contract['raw']}') != '' }}}}"
        ),
        "uses": UPLOAD_ARTIFACT_ACTION,
        "with": {
            "name": (
                f"{reviewer}-candidate-${{{{ github.run_id }}}}-"
                "${{ github.run_attempt }}"
            ),
            "path": f"${{{{ github.workspace }}}}/{contract['raw']}",
            "if-no-files-found": "ignore",
            "retention-days": "1",
            "overwrite": "false",
        },
    }


def _expected_provider_error_upload_step(
    contract: dict[str, str],
) -> dict[str, object]:
    reviewer = contract["reviewer"]
    return {
        "name": f"Upload {reviewer.capitalize()} provider error",
        "if": (
            "${{ always() "
            "&& steps.review-budget-claim.outputs.allow-invocation == 'true' "
            f"&& hashFiles('{reviewer}_provider_error.txt') != '' }}}}"
        ),
        "uses": UPLOAD_ARTIFACT_ACTION,
        "with": {
            "name": (
                f"{reviewer}-provider-error-${{{{ github.run_id }}}}-"
                "${{ github.run_attempt }}"
            ),
            "path": f"${{{{ github.workspace }}}}/{reviewer}_provider_error.txt",
            "if-no-files-found": "ignore",
            "retention-days": "1",
            "overwrite": "false",
        },
    }


def _verify_review_publication_contracts(
    documents: dict[str, dict], ref: str
) -> None:
    budget = release_supports_review_invocation_budget(ref)
    jq_state_keys = json.dumps(list(QUALITY_STATE_KEYS))
    legacy_jq_state_keys = json.dumps(list(LEGACY_QUALITY_STATE_KEYS))
    legacy_js_state_keys = ", ".join(
        repr(key) for key in LEGACY_QUALITY_STATE_KEYS
    )
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
            if canonical_step != _expected_canonicalize_step(
                contract, budget=budget, dismissals=release_supports_finding_dismissal(ref)
            ):
                raise ValueError("canonicalizer call differs")
            rejected_diagnostic_upload = _named_step(
                job,
                f"Upload rejected {contract['reviewer'].capitalize()} review diagnostic",
            )
            if (
                rejected_diagnostic_upload
                != _expected_rejected_review_diagnostic_upload_step(
                    contract, budget=budget
                )
            ):
                raise ValueError("rejected review diagnostic differs")
            # provider 오류 원문은 정규화기 소유 진단과 신뢰 등급이 달라 별도
            # 아티팩트로 올린다. gemini 만 provider 예외를 파일로 남긴다.
            if budget and contract["reviewer"] == "gemini":
                provider_error_upload = _named_step(
                    job, f"Upload {contract['reviewer'].capitalize()} provider error"
                )
                if provider_error_upload != _expected_provider_error_upload_step(
                    contract
                ):
                    raise ValueError("provider error upload differs")
                if not (
                    job["steps"].index(provider_error_upload)
                    < job["steps"].index(rejected_diagnostic_upload)
                ):
                    raise ValueError("provider error upload order differs")
            candidate_upload = None
            if budget:
                candidate_upload = _named_step(
                    job,
                    f"Upload {contract['reviewer'].capitalize()} review candidate",
                )
                if candidate_upload != _expected_review_candidate_upload_step(
                    contract
                ):
                    raise ValueError("review candidate upload differs")
                if not (
                    job["steps"].index(canonical_step)
                    < job["steps"].index(candidate_upload)
                    < job["steps"].index(rejected_diagnostic_upload)
                ):
                    raise ValueError("review candidate upload order differs")
            prepared_head_checkout = _named_step(job, "Checkout prepared review head")
            if prepared_head_checkout != _expected_prepared_head_checkout_step(
                budget=budget
            ):
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
            collector_state_keys_contract = (
                f"== {legacy_jq_state_keys}" in collector_script
                and (not budget or f"== {jq_state_keys}" in collector_script)
            )
            collector_run_event_contract = (
                '.event == "pull_request" and .head_sha == $head'
                in collector_script
                and (not budget or '.event == "workflow_dispatch"' in collector_script)
            )
            collector_contract = (
                collector_marker
                and collector_token_contract
                and collector_publisher_contract
                and collector.get("id") == contract["collector_id"]
                and collector_state_keys_contract
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
                and collector_run_event_contract
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
            expected_upsert_if = contract["upsert_if"]
            if budget:
                collector_id = contract["collector_id"]
                expected_upsert_if = (
                    f"${{{{ !cancelled() && steps.{collector_id}.outcome == 'success' && "
                    + "(steps.prepare-diff.outputs.diff-ready != 'true' || "
                    "steps.prepare-diff.outputs.diff-mode == 'unchanged' || "
                    "steps.review-budget-claim.outputs.allow-invocation == 'true') }}"
                )
            if upsert.get("if") != expected_upsert_if:
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
                != contract[
                    "upsert_sha256_v147" if budget else "upsert_sha256"
                ]
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
            upsert_state_keys_contract = (
                (
                    f"const legacyStateKeys = [{legacy_js_state_keys}];"
                    in upsert_script
                    and "const expectedStateKeys = "
                    "[...legacyStateKeys, 'review_execution'].sort();"
                    in upsert_script
                    and "review_execution: unchangedInputIsValid" in upsert_script
                    and "const modelStepEntered = ['success', 'failure'].includes(reviewOutcome);"
                    in upsert_script
                    and ": (modelStepEntered ? 'performed' : 'not_performed'),"
                    in upsert_script
                    and "`- Execution: ${state.review_execution}`" in upsert_script
                )
                if budget
                else (
                    f"const expectedStateKeys = [{legacy_js_state_keys}];"
                    in upsert_script
                )
            )
            upsert_run_event_contract = (
                "run?.event === 'pull_request'" in upsert_script
                and (not budget or "run?.event === 'workflow_dispatch'" in upsert_script)
            )
            upsert_contract = (
                f"const marker = '{marker}';" in upsert_script
                and f"const v2Marker = '{contract['v2_marker']}';" in upsert_script
                and ":v1 -->" not in upsert_script
                and upsert_state_keys_contract
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
                and upsert_run_event_contract
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
    allow_budget_state: bool = False,
) -> int:
    if not isinstance(mapping, dict):
        return 0
    sinks = 0
    for key, value in mapping.items():
        token_key = str(key).lower().replace("_", "-")
        if token_key == "token" or token_key.endswith("-token"):
            if token_key == "github-token" and allow_budget_state:
                if value != GITHUB_ACTIONS_PROVENANCE_TOKEN:
                    raise ReleaseVerificationError(
                        f"{name}:{location} budget-state token is invalid"
                    )
                continue
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


def _verify_dispatch_review_diff(tree: "VerifiedCommitTree", ref: str) -> None:
    """Require the manual review path to build and read the diff it reviews.

    Nothing described this path before v1.67, so the reviewer could be left with a
    bare checkout and the whole suite would still pass. The release now refuses a
    tree where the diff step is gone or the model no longer waits for it.
    """

    name = ".github/workflows/gemini-dispatch.yml"
    document = yaml.load(tree.read_text(name), Loader=yaml.BaseLoader)
    try:
        steps = document["jobs"]["review"]["steps"]
    except (KeyError, TypeError) as exc:
        raise ReleaseVerificationError(f"{name} has no review job") from exc
    by_name = {step.get("name"): step for step in steps if isinstance(step, dict)}
    prepare = by_name.get("Prepare review diff")
    if prepare is None or prepare.get("id") != "review_diff":
        raise ReleaseVerificationError(
            f"{name} review job does not prepare a diff for the manual review"
        )
    script = prepare.get("run", "")
    for fragment in ("git diff", "diff_ready", "diff_reason", "MAX_DIFF_BYTES"):
        if fragment not in script:
            raise ReleaseVerificationError(
                f"{name} review diff step is missing {fragment}"
            )
    model_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("google-github-actions/run-gemini-cli@")
    ]
    if len(model_steps) != 2:
        raise ReleaseVerificationError(
            f"{name} review job must invoke exactly two review models"
        )
    for step in model_steps:
        prompt = (step.get("with") or {}).get("prompt", "")
        if "steps.review_diff.outputs.diff_file" not in prompt:
            raise ReleaseVerificationError(
                f"{name} review prompt does not read the prepared diff"
            )
    primary = by_name.get("Run Gemini pull request review (primary)")
    if primary is None or primary.get("if") != (
        "steps.review_diff.outputs.diff_ready == 'true'"
    ):
        raise ReleaseVerificationError(
            f"{name} review model does not wait for a ready diff"
        )


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
    if release_supports_review_invocation_budget(ref):
        approved_actions |= {REVIEW_INVOCATION_BUDGET_ACTION}
    if release_supports_review_policy(ref):
        approved_actions |= {REVIEW_POLICY_ACTION}
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
            budget_state_step = (
                release_supports_review_invocation_budget(ref)
                and name == "gemini-auto-review.yml"
                and job_name == "gemini-review"
                and step.get("uses") == REVIEW_INVOCATION_BUDGET_ACTION
                and step_values.count("github.token") == 1
            )
            policy_step = (
                release_supports_review_policy(ref)
                and name == "gemini-auto-review.yml"
                and job_name == "check-enabled"
                and step.get("uses") == REVIEW_POLICY_ACTION
                and step_values.count("github.token") == 1
            )
            if (
                step_index not in candidates
                and "github.token" in step_values
                and not (provenance_step or budget_state_step or policy_step)
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
            budget_state_step = (
                release_supports_review_invocation_budget(ref)
                and name == "gemini-auto-review.yml"
                and job_name == "gemini-review"
                and step.get("uses") == REVIEW_INVOCATION_BUDGET_ACTION
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
                    allow_budget_state=(
                        budget_state_step and mapping_name == "with"
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
        _verify_canonicalize_review_action(tree, ref)
    if release_supports_review_policy(ref):
        _verify_review_policy_action(tree, ref)
        _verify_review_policy_callers(tree, ref)
    _verify_approved_v140_policy(tree, ref)
    _verify_manual_gemini_output_contract(tree, ref)
    if release_supports_dispatch_review_diff(ref):
        _verify_dispatch_review_diff(tree, ref)
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
        data = _load_release_yaml(
            text,
            reject_duplicate_keys=release_supports_review_policy(ref),
        )
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
    if release_supports_review_policy(ref):
        _verify_review_policy_workflows(documents)
    if release_supports_canonicalize_review(ref):
        _verify_review_publication_contracts(documents, ref)

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
        budget_release = release_supports_review_invocation_budget(ref)
        expected_prepare = {
            "actions": "read",
            "checks": "read",
            "contents": "read",
            "pull-requests": "write" if budget_release else "read",
            "issues": "write" if budget_release else "read",
        }
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
        filtered_candidate_location_contract = (
            "const filteredCandidateSummary = modelSucceeded "
            "&& filteredNewFindings.length > 0"
            in canonical_script
            and canonical_script.count(
                "? `\\n- Filtered candidate (raw): artifact "
                "\\`opencode-candidate-${runId}-${runAttempt}\\` → "
                "\\`review.md\\``"
            )
            == 1
            and (
                (
                    "${validationSummary}${carryoverSummary}"
                    "${filteredCandidateSummary}${!succeeded"
                )
                if release_supports_opencode_dismissals(ref)
                else "${validationSummary}${filteredCandidateSummary}${!succeeded"
            )
            in canonical_script
            and (
                not release_supports_opencode_dismissals(ref)
                or (
                    canonical_script.count(
                        "? `\\n- Normalization: normalized_blocks="
                        "${normalizedCarryover.length};`"
                    )
                    == 1
                    and (
                        "- Normalization: normalized_blocks=[1-9][0-9]*; "
                        "reasons=[a-z_,]+$"
                    )
                    in canonical_script
                )
            )
            and (
                "- Filtered candidate \\(raw\\): artifact "
                "`opencode-candidate-[1-9][0-9]*-[1-9][0-9]*` → "
                "`review\\.md`$"
            )
            in canonical_script
            and prepare_script.count(
                "|- Filtered candidate \\(raw\\): |"
            )
            == 2
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
            and candidate_upload.get("with", {}).get("path")
            == (
                "${{ runner.temp }}/opencode-candidate"
                if budget_release
                else "${{ runner.temp }}/opencode-candidate/review.md"
            )
            and (
                candidate_upload.get("with", {}).get("if-no-files-found") == "error"
                if budget_release
                else True
            )
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
            and (
                all(
                    fragment in canonical_script
                    for fragment in (
                        "const expectedCandidateFiles = candidateEnvelope.outcome === 'success'",
                        "? ['candidate.json', 'review.md'] : ['candidate.json']",
                        "JSON.stringify(entries.map((entry) => entry.name).sort())",
                        "!== JSON.stringify(expectedCandidateFiles)",
                        "entries.some((entry) => !entry.isFile() || entry.isSymbolicLink())",
                    )
                )
                if budget_release
                else "entries.length !== 1 || entries[0].name !== 'review.md'"
                in canonical_script
            )
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
            and (filtered_candidate_location_contract if budget_release else True)
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
    policy_guard = (
        release_supports_review_policy(ref)
        and check_job.get("outputs", {}).get("policy_run")
        == "${{ steps.review_policy.outputs.run-review }}"
        and isinstance(condition, str)
        and "needs.check-enabled.outputs.policy_run == 'true'" in condition
    )
    historical_guard = (
        not release_supports_review_policy(ref)
        and safe_output == "${{ steps.pr_scope.outputs.safe_pr }}"
        and "gh api" in scope_step.get("run", "")
        and isinstance(condition, str)
        and "needs.check-enabled.outputs.safe_pr == 'true'" in condition
    )
    if not (policy_guard or historical_guard):
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
    if release_supports_review_invocation_budget(ref):
        _verify_review_invocation_budget(tree, ref)
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
