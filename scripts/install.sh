#!/usr/bin/env bash
set -euo pipefail

# DevAI Installer — Linux/macOS
# Downloads precompiled Go binary + portable Python, creates venv, installs deps.
# No Go or Python required on the host system.

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ── Globals ───────────────────────────────────────────────────────────────────
INSTALL_DIR="${HOME}/.local/share/devai"
STATE_DIR=""                       # defaults to ${INSTALL_DIR}/state after parsing
TMP_DIR=""
REPO="snaven10/devai-context-engine"
GITHUB_API="https://api.github.com/repos/${REPO}/releases"
PYTHON_STANDALONE_REPO="astral-sh/python-build-standalone"
PYTHON_VERSION="3.12"

# Flags / wizard answers
GPU=false
VERSION=""
UNINSTALL=false
ASSUME_YES=false
MODEL="minilm-l6"
CLIENT="claude"                    # claude | cursor | both | none
SCOPE="global"                     # global | project
INSTALL_HOOKS=true
INTERACTIVE=false
ALLOW_NO_ML=false

# ── Helpers ───────────────────────────────────────────────────────────────────
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()    { echo -e "\n${CYAN}${BOLD}▸ $*${NC}"; }

die() {
    error "$@"
    exit 1
}

# ── Interactive Wizard ──────────────────────────────────────────────────────────
# ask PROMPT DEFAULT -> echoes the answer (default when non-interactive or blank input)
ask() {
    local prompt="$1" default="$2" reply
    if [[ "${INTERACTIVE}" != true ]]; then
        echo "${default}"; return
    fi
    read -rp "$(echo -e "${CYAN}?${NC} ${prompt} ${BOLD}[${default}]${NC}: ")" reply || true
    echo "${reply:-${default}}"
}

# ask_yesno PROMPT DEFAULT(true|false) -> sets REPLY_BOOL
ask_yesno() {
    local prompt="$1" default="$2" def_label reply
    if [[ "${default}" == true ]]; then def_label="Y/n"; else def_label="y/N"; fi
    if [[ "${INTERACTIVE}" != true ]]; then REPLY_BOOL="${default}"; return; fi
    read -rp "$(echo -e "${CYAN}?${NC} ${prompt} ${BOLD}[${def_label}]${NC}: ")" reply || true
    case "${reply}" in
        [Yy]*) REPLY_BOOL=true ;;
        [Nn]*) REPLY_BOOL=false ;;
        *)     REPLY_BOOL="${default}" ;;
    esac
}

run_wizard() {
    if [[ "${INTERACTIVE}" != true ]]; then return 0; fi
    step "Configuration"
    INSTALL_DIR="$(ask "Install directory" "${INSTALL_DIR}")"
    derive_paths
    STATE_DIR="$(ask "State directory (vectors/memory)" "${STATE_DIR}")"

    ask_yesno "Use GPU (CUDA) PyTorch? (No = CPU-only)" "${GPU}"; GPU="${REPLY_BOOL}"
    MODEL="$(ask "Embedding model (minilm-l6 | ml-mpnet)" "${MODEL}")"
    CLIENT="$(ask "Configure AI client (claude | cursor | both | none)" "${CLIENT}")"
    if [[ "${CLIENT}" != none ]]; then
        SCOPE="$(ask "Claude config scope (global | project)" "${SCOPE}")"
    fi
    ask_yesno "Install git auto-index hook?" "${INSTALL_HOOKS}"; INSTALL_HOOKS="${REPLY_BOOL}"
}

cleanup() {
    if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
        rm -rf "${TMP_DIR}"
    fi
}

trap cleanup EXIT

# ── Argument Parsing ──────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: install.sh [OPTIONS]

Options:
  --install-dir DIR   Install location (default: ~/.local/share/devai)
  --state-dir DIR     State location for vectors/memory (default: <install-dir>/state)
  --gpu               Install PyTorch with CUDA support (default: CPU-only)
  --model KEY         Embedding model: minilm-l6 (default) or ml-mpnet (multilingual)
  --client NAME       Configure AI client: claude (default), cursor, both, none
  --scope SCOPE       Claude config location: global (~/.claude.json, default) or project (.mcp.json)
  --hooks             Install the git auto-index post-commit hook (default)
  --no-hooks          Skip the git auto-index hook
  --version TAG       Install a specific release version (default: latest)
  --yes, -y           Accept all defaults; never prompt (implied when no TTY)
  --uninstall         Remove DevAI and all its files
  -h, --help          Show this help message
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        --state-dir)   STATE_DIR="$2"; shift 2 ;;
        --gpu)         GPU=true; shift ;;
        --model)       MODEL="$2"; shift 2 ;;
        --client)      CLIENT="$2"; shift 2 ;;
        --scope)       SCOPE="$2"; shift 2 ;;
        --hooks)       INSTALL_HOOKS=true; shift ;;
        --no-hooks)    INSTALL_HOOKS=false; shift ;;
        --version)     VERSION="$2"; shift 2 ;;
        --yes|-y)      ASSUME_YES=true; shift ;;
        --allow-no-ml) ALLOW_NO_ML=true; shift ;;
        --uninstall)   UNINSTALL=true; shift ;;
        -h|--help)     usage ;;
        *)             die "Unknown option: $1. Use --help for usage." ;;
    esac
done

derive_paths() {
    BIN_DIR="${INSTALL_DIR}/bin"
    PYTHON_DIR="${INSTALL_DIR}/python"
    VENV_DIR="${PYTHON_DIR}/venv"
    [[ -z "${STATE_DIR}" ]] && STATE_DIR="${INSTALL_DIR}/state"
}

# Needed by the --uninstall block below; main() re-derives after the wizard.
derive_paths

# ── Uninstall ─────────────────────────────────────────────────────────────────
if [[ "${UNINSTALL}" == true ]]; then
    step "Uninstalling DevAI"
    if [[ -d "${INSTALL_DIR}" ]]; then
        rm -rf "${INSTALL_DIR}"
        success "Removed ${INSTALL_DIR}"
    else
        warn "Nothing to remove — ${INSTALL_DIR} does not exist."
    fi
    echo ""
    warn "Remember to remove ${BIN_DIR} from your PATH in .bashrc/.zshrc"
    exit 0
fi

# ── System Detection ─────────────────────────────────────────────────────────
detect_platform() {
    local uname_os uname_arch

    uname_os="$(uname -s)"
    uname_arch="$(uname -m)"

    case "${uname_os}" in
        Linux*)  OS="linux" ;;
        Darwin*) OS="darwin" ;;
        *)       die "Unsupported OS: ${uname_os}. Only Linux and macOS are supported." ;;
    esac

    case "${uname_arch}" in
        x86_64)  ARCH="amd64"; PYTHON_ARCH="x86_64" ;;
        aarch64) ARCH="arm64";  PYTHON_ARCH="aarch64" ;;
        arm64)   ARCH="arm64";  PYTHON_ARCH="aarch64" ;;
        *)       die "Unsupported architecture: ${uname_arch}" ;;
    esac

    # python-build-standalone uses different OS names
    case "${OS}" in
        linux)  PYTHON_OS="unknown-linux-gnu" ;;
        darwin) PYTHON_OS="apple-darwin" ;;
    esac

    info "Detected: OS=${OS} ARCH=${ARCH}"
}

# ── Dependency Checks ─────────────────────────────────────────────────────────
check_deps() {
    local missing=()

    if ! command -v curl &>/dev/null && ! command -v wget &>/dev/null; then
        missing+=("curl or wget")
    fi

    if ! command -v tar &>/dev/null; then
        missing+=("tar")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing required tools: ${missing[*]}. Please install them first."
    fi
}

# ── Download Helper ───────────────────────────────────────────────────────────
download() {
    local url="$1"
    local dest="$2"

    if command -v curl &>/dev/null; then
        curl -fSL --retry 3 --retry-delay 2 -o "${dest}" "${url}"
    elif command -v wget &>/dev/null; then
        wget -q --tries=3 -O "${dest}" "${url}"
    fi
}

# Fetch JSON from a URL, return body on stdout
fetch_json() {
    local url="$1"

    if command -v curl &>/dev/null; then
        curl -fsSL --retry 3 --retry-delay 2 "${url}"
    elif command -v wget &>/dev/null; then
        wget -qO- --tries=3 "${url}"
    fi
}

# ── Resolve DevAI Release ────────────────────────────────────────────────────
resolve_devai_version() {
    step "Resolving DevAI release"

    if [[ -n "${VERSION}" ]]; then
        RELEASE_TAG="${VERSION}"
        info "Using specified version: ${RELEASE_TAG}"
        RELEASE_INFO="$(fetch_json "${GITHUB_API}/tags/${RELEASE_TAG}")" || true
    else
        info "Fetching latest release from GitHub..."
        RELEASE_INFO="$(fetch_json "${GITHUB_API}/latest")" || die "Failed to fetch latest release info. Check your network."

        RELEASE_TAG="$(echo "${RELEASE_INFO}" | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)"

        if [[ -z "${RELEASE_TAG}" ]]; then
            die "Could not determine latest release tag."
        fi
        info "Latest release: ${RELEASE_TAG}"
    fi
}

# ── Download & Install Go Binary ─────────────────────────────────────────────
install_binary() {
    step "Installing DevAI binary"

    local archive_name="devai_${OS}_${ARCH}.tar.gz"
    local download_url="https://github.com/${REPO}/releases/download/${RELEASE_TAG}/${archive_name}"
    local archive_path="${TMP_DIR}/${archive_name}"

    info "Downloading ${archive_name}..."
    download "${download_url}" "${archive_path}" || die "Failed to download binary from ${download_url}"

    mkdir -p "${BIN_DIR}"
    tar -xzf "${archive_path}" -C "${TMP_DIR}"

    # Find the devai binary in the extracted content
    local binary
    binary="$(find "${TMP_DIR}" -name 'devai' -type f ! -path '*/\.*' | head -1)"
    if [[ -z "${binary}" ]]; then
        die "Could not find devai binary in archive."
    fi

    cp "${binary}" "${BIN_DIR}/devai"
    chmod +x "${BIN_DIR}/devai"

    success "Installed devai binary to ${BIN_DIR}/devai"
}

# ── Download & Extract Portable Python ────────────────────────────────────────
install_python() {
    step "Installing portable Python ${PYTHON_VERSION}"

    # If python already exists and works, skip
    if [[ -x "${PYTHON_DIR}/bin/python3" ]]; then
        local existing_ver
        existing_ver="$("${PYTHON_DIR}/bin/python3" --version 2>/dev/null || true)"
        if [[ "${existing_ver}" == *"${PYTHON_VERSION}"* ]]; then
            success "Portable Python ${PYTHON_VERSION} already installed — skipping."
            return 0
        fi
    fi

    info "Fetching latest python-build-standalone release..."
    local response
    response="$(fetch_json "https://api.github.com/repos/${PYTHON_STANDALONE_REPO}/releases/latest")" \
        || die "Failed to fetch python-build-standalone release info."

    # Find a matching asset URL for install_only_stripped
    # Pattern: cpython-3.12.X+YYYYMMDD-{arch}-{os}-install_only_stripped.tar.gz
    local asset_url
    asset_url="$(echo "${response}" | grep -o '"browser_download_url"[[:space:]]*:[[:space:]]*"[^"]*"' \
        | grep "cpython-${PYTHON_VERSION}" \
        | grep "${PYTHON_ARCH}-${PYTHON_OS}" \
        | grep "install_only_stripped" \
        | head -1 \
        | cut -d'"' -f4)"

    # Fallback to install_only (non-stripped) if stripped not available
    if [[ -z "${asset_url}" ]]; then
        asset_url="$(echo "${response}" | grep -o '"browser_download_url"[[:space:]]*:[[:space:]]*"[^"]*"' \
            | grep "cpython-${PYTHON_VERSION}" \
            | grep "${PYTHON_ARCH}-${PYTHON_OS}" \
            | grep "install_only" \
            | grep -v "debug" \
            | head -1 \
            | cut -d'"' -f4)"
    fi

    if [[ -z "${asset_url}" ]]; then
        die "Could not find Python ${PYTHON_VERSION} build for ${PYTHON_ARCH}-${PYTHON_OS}"
    fi

    local archive_name
    archive_name="$(basename "${asset_url}")"
    local archive_path="${TMP_DIR}/${archive_name}"

    info "Downloading ${archive_name}..."
    download "${asset_url}" "${archive_path}" || die "Failed to download portable Python."

    mkdir -p "${PYTHON_DIR}"
    # python-build-standalone extracts to a "python/" directory
    tar -xzf "${archive_path}" -C "${PYTHON_DIR}" --strip-components=1

    if [[ ! -x "${PYTHON_DIR}/bin/python3" ]]; then
        die "Python extraction failed — bin/python3 not found."
    fi

    success "Installed portable Python to ${PYTHON_DIR}"
}

# ── Create Virtual Environment ────────────────────────────────────────────────
create_venv() {
    step "Creating Python virtual environment"

    if [[ -d "${VENV_DIR}" && -x "${VENV_DIR}/bin/python" ]]; then
        success "Virtual environment already exists — skipping."
        return 0
    fi

    "${PYTHON_DIR}/bin/python3" -m venv "${VENV_DIR}" \
        || die "Failed to create virtual environment."

    success "Created venv at ${VENV_DIR}"
}

# ── Install Python Dependencies ──────────────────────────────────────────────
install_python_deps() {
    step "Installing Python dependencies"

    local pip="${VENV_DIR}/bin/pip"
    local req_file

    # Upgrade pip first
    "${VENV_DIR}/bin/python" -m pip install --upgrade pip --quiet || true

    if [[ "${GPU}" == true ]]; then
        info "Installing with GPU (CUDA) PyTorch support"
        req_file="requirements-gpu.txt"
    else
        info "Installing with CPU-only PyTorch (use --gpu for CUDA)"
        req_file="requirements-cpu.txt"
    fi

    # Try to download requirements from release assets first, fallback to bundled
    local req_url="https://github.com/${REPO}/releases/download/${RELEASE_TAG}/${req_file}"
    local req_path="${TMP_DIR}/${req_file}"
    local use_remote=false

    if download "${req_url}" "${req_path}" 2>/dev/null; then
        use_remote=true
    fi

    if [[ "${use_remote}" == true ]]; then
        info "Using requirements from release assets"
        "${pip}" install -r "${req_path}" --quiet \
            || die "Failed to install Python dependencies."
    else
        # Fallback: install inline requirements
        info "Release requirements not found — using bundled list"
        local script_dir
        script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        local local_req="${script_dir}/${req_file}"

        if [[ -f "${local_req}" ]]; then
            "${pip}" install -r "${local_req}" --quiet \
                || die "Failed to install Python dependencies."
        else
            die "Could not find requirements file. Looked in release assets and ${local_req}"
        fi
    fi

    # Install devai_ml wheel from release
    local wheel_url
    wheel_url=$(echo "${RELEASE_INFO}" | grep -o '"browser_download_url":\s*"[^"]*devai_ml-[^"]*\.whl"' | head -1 | cut -d'"' -f4)
    if [[ -n "${wheel_url}" ]]; then
        local wheel_name
        wheel_name=$(basename "${wheel_url}")
        local wheel_path="${TMP_DIR}/${wheel_name}"
        info "Installing devai_ml wheel..."
        download "${wheel_url}" "${wheel_path}"
        "${pip}" install "${wheel_path}" --quiet \
            || die "Failed to install devai_ml wheel."
    else
        if [[ "${ALLOW_NO_ML}" == true ]]; then
            warn "devai_ml wheel not found — continuing without ML (search/index will not work)."
        else
            die "devai_ml wheel not found in release assets. ML features (embeddings, search, indexing) will not work. Re-run with --allow-no-ml to install anyway."
        fi
    fi

    success "Python dependencies installed"
}

# ── PATH Setup ────────────────────────────────────────────────────────────────
setup_path() {
    step "Checking PATH"

    if echo "${PATH}" | tr ':' '\n' | grep -qx "${BIN_DIR}"; then
        success "${BIN_DIR} already in PATH"
        return 0
    fi

    local shell_rc=""
    case "${SHELL}" in
        */zsh)  shell_rc="${HOME}/.zshrc" ;;
        */bash) shell_rc="${HOME}/.bashrc" ;;
        *)      shell_rc="${HOME}/.profile" ;;
    esac

    local path_line="export PATH=\"${BIN_DIR}:\${PATH}\""

    if [[ -f "${shell_rc}" ]] && grep -qF "${BIN_DIR}" "${shell_rc}" 2>/dev/null; then
        success "PATH entry already in ${shell_rc}"
    else
        warn "Add the following to ${shell_rc} (or your shell config):"
        echo ""
        echo "  ${path_line}"
        echo ""
        ask_yesno "Add it automatically?" true
        if [[ "${REPLY_BOOL}" != true ]]; then
            warn "Skipped. Add it manually to use 'devai' from anywhere."
        else
            echo "" >> "${shell_rc}"
            echo "# DevAI" >> "${shell_rc}"
            echo "${path_line}" >> "${shell_rc}"
            success "Added to ${shell_rc} — restart your shell or run: source ${shell_rc}"
        fi
    fi
}

# ── Configure AI Client (delegates to the binary) ───────────────────────────────
configure_client() {
    [[ "${CLIENT}" == none ]] && { info "Skipping AI client configuration (--client none)."; return 0; }
    step "Configuring AI client(s): ${CLIENT}"

    local client_flags=()
    case "${CLIENT}" in
        claude) client_flags=(--claude) ;;
        cursor) client_flags=(--cursor) ;;
        both)   client_flags=(--all) ;;
        *)      warn "Unknown --client '${CLIENT}', defaulting to claude"; client_flags=(--claude) ;;
    esac

    local env_flags=(
        --env "DEVAI_STATE_DIR=${STATE_DIR}"
        --env "DEVAI_EMBEDDING_MODEL=${MODEL}"
        --env "DEVAI_EMBED_MAX_CHARS=2048"
    )
    if [[ "${MODEL}" == "ml-mpnet" ]]; then
        env_flags+=(--env "DEVAI_RERANK_MODEL=ms-marco-MultiBERT-L-12")
    fi

    "${BIN_DIR}/devai" server configure "${client_flags[@]}" --scope "${SCOPE}" "${env_flags[@]}" \
        || warn "Client configuration failed — run 'devai server configure' manually."
}

# ── Install git hooks (delegates to the binary) ─────────────────────────────────
maybe_install_hooks() {
    if [[ "${INSTALL_HOOKS}" != true ]]; then return 0; fi
    if git -C "$(pwd)" rev-parse --is-inside-work-tree &>/dev/null; then
        step "Installing git auto-index hook"
        "${BIN_DIR}/devai" hooks install || warn "Hook install failed — run 'devai hooks install' manually."
    else
        info "Not a git repo here — skipping auto-index hook. Run 'devai hooks install' inside a repo later."
    fi
}

# ── Print Summary ─────────────────────────────────────────────────────────────
print_summary() {
    local devai_version
    devai_version="$("${BIN_DIR}/devai" version 2>/dev/null || echo "${RELEASE_TAG}")"
    local python_version
    python_version="$("${VENV_DIR}/bin/python" --version 2>/dev/null || echo "Python ${PYTHON_VERSION}")"

    echo ""
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║       DevAI installed successfully!          ║${NC}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BOLD}Binary:${NC}  ${BIN_DIR}/devai (${devai_version})"
    echo -e "  ${BOLD}Python:${NC}  ${python_version}"
    echo -e "  ${BOLD}Venv:${NC}    ${VENV_DIR}"
    echo -e "  ${BOLD}PyTorch:${NC} $(if [[ "${GPU}" == true ]]; then echo "GPU (CUDA)"; else echo "CPU-only"; fi)"
    echo -e "  ${BOLD}State:${NC}   ${STATE_DIR}"
    echo -e "  ${BOLD}Model:${NC}   ${MODEL}"
    echo -e "  ${BOLD}Client:${NC}  ${CLIENT} (scope: ${SCOPE})"
    echo ""
    echo -e "  Run ${CYAN}devai --help${NC} to get started."
    echo ""
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    echo -e "${BOLD}DevAI Installer${NC}"
    echo ""

    if [[ -t 0 && "${ASSUME_YES}" != true ]]; then
        INTERACTIVE=true
    fi
    run_wizard
    derive_paths   # re-derive in case the wizard changed INSTALL_DIR

    check_deps
    detect_platform

    TMP_DIR="$(mktemp -d)"
    info "Working in ${TMP_DIR}"

    resolve_devai_version
    install_binary
    install_python
    create_venv
    install_python_deps
    configure_client
    maybe_install_hooks
    setup_path
    print_summary
}

main
