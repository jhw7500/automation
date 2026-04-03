#!/bin/bash
# GitHub 저장소 공통 워크플로우 + 시크릿 일괄 설정 스크립트
# 설정: workflow-config.json (저장소/워크플로우/시크릿/버전 관리)
#
# 사용법:
#   ./setup-github-workflows.sh                  # 전체 실행
#   ./setup-github-workflows.sh --dry-run        # 미리보기
#   ./setup-github-workflows.sh --secrets-only   # 시크릿만
#   ./setup-github-workflows.sh --workflows-only # 워크플로우만
#   ./setup-github-workflows.sh --repo gstApp    # 특정 저장소만
#   ./setup-github-workflows.sh --skip-secret GEMINI_API_KEY  # 특정 시크릿 제외
#   ./setup-github-workflows.sh --skip-workflow claude.yml     # 특정 워크플로우 제외
#   ./setup-github-workflows.sh --projects-root /path/to/root  # 프로젝트 루트 지정
#
# 사전 조건:
#   - gh CLI 로그인 (gh auth login)
#   - jq 설치
#   - 시크릿: 환경변수 또는 실행 시 입력

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTOMATION_ROOT="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$SCRIPT_DIR/workflow-config.json"

# ── 옵션 파싱 ──
DRY_RUN=false
SECRETS_ONLY=false
WORKFLOWS_ONLY=false
FILTER_REPO=""
PROJECTS_ROOT=""
SKIP_SECRETS=()
SKIP_WORKFLOWS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)          DRY_RUN=true ;;
        --secrets-only)     SECRETS_ONLY=true ;;
        --workflows-only)   WORKFLOWS_ONLY=true ;;
        --repo)             FILTER_REPO="$2"; shift ;;
        --projects-root)    PROJECTS_ROOT="$2"; shift ;;
        --skip-secret)      SKIP_SECRETS+=("$2"); shift ;;
        --skip-workflow)    SKIP_WORKFLOWS+=("$2"); shift ;;
        --help|-h)
            sed -n '2,14p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "알 수 없는 옵션: $1 (--help 참조)"; exit 1 ;;
    esac
    shift
done

# ── 설정 파일 로드 ──
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: 설정 파일 없음: $CONFIG_FILE"
    exit 1
fi

if ! command -v jq &>/dev/null; then
    echo "ERROR: jq가 필요합니다. sudo apt install jq"
    exit 1
fi

GH_OWNER=$(jq -r '.gh_owner' "$CONFIG_FILE")
AUTOMATION_REF=$(jq -r '.automation_ref' "$CONFIG_FILE")
TEMPLATE_DIR_REL=$(jq -r '.template_dir' "$CONFIG_FILE")
TEMPLATE_DIR="$AUTOMATION_ROOT/$TEMPLATE_DIR_REL"

# 프로젝트 루트: --projects-root > config > automation 상위 디렉토리
if [ -z "$PROJECTS_ROOT" ]; then
    PROJECTS_ROOT=$(jq -r '.projects_root // empty' "$CONFIG_FILE" 2>/dev/null || true)
fi
if [ -z "$PROJECTS_ROOT" ]; then
    PROJECTS_ROOT="$(dirname "$AUTOMATION_ROOT")"
fi

# 배열 로드
mapfile -t ALL_WORKFLOWS < <(jq -r '.workflows[]' "$CONFIG_FILE")
mapfile -t ALL_EXTRA_FILES < <(jq -r '.extra_files[]' "$CONFIG_FILE")
mapfile -t ALL_SECRETS < <(jq -r '.secrets[]' "$CONFIG_FILE")
mapfile -t ALL_REPOS < <(jq -r '.repos | keys[]' "$CONFIG_FILE")

# ── skip 필터 적용 ──
is_skipped() {
    local item="$1"
    shift
    local arr=("$@")
    for s in "${arr[@]}"; do
        [ "$s" = "$item" ] && return 0
    done
    return 1
}

WORKFLOWS=()
for wf in "${ALL_WORKFLOWS[@]}"; do
    is_skipped "$wf" "${SKIP_WORKFLOWS[@]+"${SKIP_WORKFLOWS[@]}"}" || WORKFLOWS+=("$wf")
done

SECRETS=()
for sk in "${ALL_SECRETS[@]}"; do
    is_skipped "$sk" "${SKIP_SECRETS[@]+"${SKIP_SECRETS[@]}"}" || SECRETS+=("$sk")
done

# ── 대상 저장소 필터링 ──
TARGET_REPOS=()
for repo in "${ALL_REPOS[@]}"; do
    if [ -n "$FILTER_REPO" ] && [ "$repo" != "$FILTER_REPO" ]; then
        continue
    fi
    TARGET_REPOS+=("$repo")
done

if [ ${#TARGET_REPOS[@]} -eq 0 ]; then
    echo "ERROR: 대상 저장소가 없습니다."
    [ -n "$FILTER_REPO" ] && echo "  --repo $FILTER_REPO 가 config에 없습니다."
    exit 1
fi

# ── 시크릿 값 수집 ──
declare -A SECRET_VALUES

if [ "$WORKFLOWS_ONLY" = false ] && [ ${#SECRETS[@]} -gt 0 ]; then
    for SECRET_NAME in "${SECRETS[@]}"; do
        VALUE="${!SECRET_NAME:-}"
        if [ -z "$VALUE" ]; then
            read -rsp "$SECRET_NAME: " VALUE
            echo
        fi
        if [ -z "$VALUE" ]; then
            echo "ERROR: $SECRET_NAME 값이 비어있습니다."
            exit 1
        fi
        SECRET_VALUES["$SECRET_NAME"]="$VALUE"
    done
fi

# ── 템플릿 검증 ──
if [ "$SECRETS_ONLY" = false ]; then
    if [ ! -d "$TEMPLATE_DIR/workflows" ]; then
        echo "ERROR: 템플릿 워크플로우 디렉토리 없음: $TEMPLATE_DIR/workflows"
        exit 1
    fi
fi

# ── 요약 출력 ──
echo "════════════════════════════════════════════════"
echo " GitHub 워크플로우 + 시크릿 일괄 설정"
echo "════════════════════════════════════════════════"
echo "설정파일:     $(basename "$CONFIG_FILE")"
echo "템플릿:       $TEMPLATE_DIR_REL"
echo "automation:   $AUTOMATION_REF"
echo "프로젝트:     $PROJECTS_ROOT"
echo "대상:         ${#TARGET_REPOS[@]}개 저장소"
[ "$SECRETS_ONLY" = false ] && echo "워크플로우:   ${WORKFLOWS[*]:-없음}"
[ "$SECRETS_ONLY" = false ] && echo "추가파일:     ${ALL_EXTRA_FILES[*]:-없음}"
[ "$WORKFLOWS_ONLY" = false ] && echo "시크릿:       ${SECRETS[*]:-없음}"
[ ${#SKIP_WORKFLOWS[@]} -gt 0 ] && echo "제외(WF):     ${SKIP_WORKFLOWS[*]}"
[ ${#SKIP_SECRETS[@]} -gt 0 ] && echo "제외(시크릿): ${SKIP_SECRETS[*]}"
[ "$DRY_RUN" = true ] && echo "모드:         DRY-RUN (실제 변경 없음)"
echo "────────────────────────────────────────────────"

FAIL_COUNT=0
SUCCESS_COUNT=0
SKIP_COUNT=0

for repo in "${TARGET_REPOS[@]}"; do
    REPO_PATH="$PROJECTS_ROOT/$repo"
    REPO_WF_ENABLED=$(jq -r ".repos[\"$repo\"].workflows // true" "$CONFIG_FILE")
    REPO_SECRET_ENABLED=$(jq -r ".repos[\"$repo\"].secrets // true" "$CONFIG_FILE")

    echo ""
    echo ">>> [$repo]"

    # 저장소 존재 확인
    if [ ! -d "$REPO_PATH/.git" ]; then
        echo "  SKIP: 로컬 저장소 없음 ($REPO_PATH)"
        ((SKIP_COUNT++)) || true
        continue
    fi

    # GitHub 리모트 확인
    REMOTE=$(git -C "$REPO_PATH" remote get-url origin 2>/dev/null || echo "")
    if ! echo "$REMOTE" | grep -q "github.com"; then
        echo "  SKIP: GitHub 리모트 아님 ($REMOTE)"
        ((SKIP_COUNT++)) || true
        continue
    fi

    FULL_REPO="$GH_OWNER/$repo"

    # ── 워크플로우 ──
    if [ "$SECRETS_ONLY" = false ] && [ "$REPO_WF_ENABLED" = "true" ] && [ ${#WORKFLOWS[@]} -gt 0 ]; then
        if [ "$DRY_RUN" = true ]; then
            for wf in "${WORKFLOWS[@]}"; do
                echo "  [DRY] 워크플로우: $wf"
            done
            for ef in "${ALL_EXTRA_FILES[@]}"; do
                echo "  [DRY] 추가파일: $ef"
            done
        else
            mkdir -p "$REPO_PATH/.github/workflows"

            for wf in "${WORKFLOWS[@]}"; do
                SRC="$TEMPLATE_DIR/workflows/$wf"
                if [ -f "$SRC" ]; then
                    cp "$SRC" "$REPO_PATH/.github/workflows/$wf"
                    echo "  워크플로우: $wf"
                else
                    echo "  WARN: 템플릿 없음 $wf"
                fi
            done

            for ef in "${ALL_EXTRA_FILES[@]}"; do
                SRC="$TEMPLATE_DIR/$ef"
                if [ -f "$SRC" ]; then
                    cp "$SRC" "$REPO_PATH/.github/$ef"
                    echo "  추가파일: $ef"
                fi
            done

            CHANGES=$(git -C "$REPO_PATH" status --porcelain .github/ 2>/dev/null)
            if [ -n "$CHANGES" ]; then
                git -C "$REPO_PATH" add .github/
                git -C "$REPO_PATH" commit -m "ci: 공통 워크플로우 적용 (Claude + Gemini, automation $AUTOMATION_REF)"
                echo "  커밋 완료"

                BRANCH=$(git -C "$REPO_PATH" branch --show-current)
                if ! git -C "$REPO_PATH" push 2>/dev/null; then
                    git -C "$REPO_PATH" push -u origin "$BRANCH" 2>/dev/null || {
                        echo "  WARN: push 실패"
                        ((FAIL_COUNT++)) || true
                        continue
                    }
                fi
                echo "  push 완료 ($BRANCH)"
            else
                echo "  워크플로우 변경 없음"
            fi
        fi
    elif [ "$SECRETS_ONLY" = false ] && [ "$REPO_WF_ENABLED" != "true" ]; then
        echo "  워크플로우: config에서 비활성화"
    fi

    # ── 시크릿 ──
    if [ "$WORKFLOWS_ONLY" = false ] && [ "$REPO_SECRET_ENABLED" = "true" ] && [ ${#SECRETS[@]} -gt 0 ]; then
        for SECRET_NAME in "${SECRETS[@]}"; do
            if [ "$DRY_RUN" = true ]; then
                echo "  [DRY] 시크릿: $SECRET_NAME → $FULL_REPO"
            else
                echo "${SECRET_VALUES[$SECRET_NAME]}" | gh secret set "$SECRET_NAME" -R "$FULL_REPO" 2>/dev/null && \
                    echo "  시크릿: $SECRET_NAME ✓" || \
                    echo "  WARN: $SECRET_NAME 설정 실패"
            fi
        done
    elif [ "$WORKFLOWS_ONLY" = false ] && [ "$REPO_SECRET_ENABLED" != "true" ]; then
        echo "  시크릿: config에서 비활성화"
    fi

    ((SUCCESS_COUNT++)) || true
done

echo ""
echo "════════════════════════════════════════════════"
echo " 결과: ${SUCCESS_COUNT} 성공, ${FAIL_COUNT} 실패, ${SKIP_COUNT} 건너뜀"
echo "════════════════════════════════════════════════"
