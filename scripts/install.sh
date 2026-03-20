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
BIN_DIR="${INSTALL_DIR}/bin"
PYTHON_DIR="${INSTALL_DIR}/python"
VENV_DIR="${PYTHON_DIR}/venv"
TMP_DIR=""
REPO="snaven10/devai-context-engine"
GITHUB_API="https://api.github.com/repos/${REPO}/releases"
PYTHON_STANDALONE_REPO="astral-sh/python-build-standalone"
PYTHON_VERSION="3.12"

# Flags
GPU=false
VERSION=""
UNINSTALL=false

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
  --gpu         Install PyTorch with CUDA support (default: CPU-only)
  --version TAG Install a specific release version (default: latest)
  --uninstall   Remove DevAI and all its files
  -h, --help    Show this help message
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --gpu)       GPU=true; shift ;;
        --version)   VERSION="$2"; shift 2 ;;
        --uninstall) UNINSTALL=true; shift ;;
        -h|--help)   usage ;;
        *)           die "Unknown option: $1. Use --help for usage." ;;
    esac
done

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
    else
        info "Fetching latest release from GitHub..."
        local response
        response="$(fetch_json "${GITHUB_API}/latest")" || die "Failed to fetch latest release info. Check your network."

        RELEASE_TAG="$(echo "${response}" | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)"

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
        read -rp "Add it automatically? [Y/n] " answer
        if [[ "${answer}" =~ ^[Nn] ]]; then
            warn "Skipped. Add it manually to use 'devai' from anywhere."
        else
            echo "" >> "${shell_rc}"
            echo "# DevAI" >> "${shell_rc}"
            echo "${path_line}" >> "${shell_rc}"
            success "Added to ${shell_rc} — restart your shell or run: source ${shell_rc}"
        fi
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
    echo ""
    echo -e "  Run ${CYAN}devai --help${NC} to get started."
    echo ""
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    echo -e "${BOLD}DevAI Installer${NC}"
    echo ""

    check_deps
    detect_platform

    TMP_DIR="$(mktemp -d)"
    info "Working in ${TMP_DIR}"

    resolve_devai_version
    install_binary
    install_python
    create_venv
    install_python_deps
    setup_path
    print_summary
}

main
