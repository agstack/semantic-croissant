#!/usr/bin/env bash
# ==============================================================================
# Semantic Croissant - Backup Script for Elasticsearch and QLever Indexes
#
# Creates .tar.gz archives:
#   1. Elasticsearch indexes from http://10.147.18.24:9205 -> dumps/elasticsearch-indexes.tar.gz
#   2. QLever indexes from http://10.147.18.24:7051 -> dumps/qlever-cdif-indexes.tar.gz
#
# Archives are placed in dumps/ and ready for restoration.
# ==============================================================================

set -euo pipefail

# ANSI colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DUMPS_DIR="${ROOT_DIR}/dumps"

mkdir -p "${DUMPS_DIR}"

ES_HOST="${ES_HOST:-http://10.147.18.24:9205}"
QLEVER_HOST="${QLEVER_HOST:-http://10.147.18.24:7051}"

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Detect compression command (pigz if available for fast multi-threading, else gzip)
if command -v pigz >/dev/null 2>&1; then
    COMPRESS_CMD="pigz -p $(nproc || echo 4)"
else
    COMPRESS_CMD="gzip -c"
fi

echo "=================================================================="
echo "   Semantic Croissant - Database & Knowledge Graph Backup"
echo "=================================================================="
log_info "Target dumps directory: ${DUMPS_DIR}"
log_info "Elasticsearch Endpoint: ${ES_HOST}"
log_info "QLever SPARQL Endpoint: ${QLEVER_HOST}"
echo ""

# ------------------------------------------------------------------------------
# 1. Backup Elasticsearch Indexes
# ------------------------------------------------------------------------------
log_info "Step 1: Backing up Elasticsearch indexes..."

# Check ES connectivity & flush synced data
if curl -sf "${ES_HOST}/_cat/indices" >/dev/null 2>&1; then
    log_info "Flushing Elasticsearch data to disk..."
    curl -sf -X POST "${ES_HOST}/_flush" >/dev/null 2>&1 || true
else
    log_warn "Elasticsearch at ${ES_HOST} is not reachable via HTTP. Attempting disk backup directly..."
fi

# Locate ES data directory
ES_DATA_DIR=""
POSSIBLE_ES_DIRS=(
    "${ROOT_DIR}/qlever-tests/volumes/asknowmuseum/es"
    "${ROOT_DIR}/volumes/asknowmuseum/es"
    "${ROOT_DIR}/volumes/codata/es"
    "${ROOT_DIR}/qlever-tests/volumes/codata/es"
    "${ROOT_DIR}/qlever-tests/volumes/es"
)

for dir in "${POSSIBLE_ES_DIRS[@]}"; do
    if [ -d "${dir}/indices" ]; then
        ES_DATA_DIR="$(readlink -f "${dir}")"
        break
    fi
done

if [ -z "${ES_DATA_DIR}" ] || [ ! -d "${ES_DATA_DIR}" ]; then
    log_error "Could not locate Elasticsearch data directory containing 'indices'."
    exit 1
fi

ES_ARCHIVE="${DUMPS_DIR}/elasticsearch-indexes.tar.gz"
log_info "Source directory: ${ES_DATA_DIR}"
log_info "Writing archive: ${ES_ARCHIVE}..."

tar -C "${ES_DATA_DIR}" \
    --exclude="*.hprof" \
    --exclude="node.lock" \
    --warning=no-file-changed \
    --use-compress-program="${COMPRESS_CMD}" \
    -cf "${ES_ARCHIVE}" . || [ $? -eq 1 ]

ES_SIZE="$(du -h "${ES_ARCHIVE}" | awk '{print $1}')"
log_success "Elasticsearch backup complete: ${ES_ARCHIVE} (${ES_SIZE})"
echo ""

# ------------------------------------------------------------------------------
# 2. Backup QLever Indexes
# ------------------------------------------------------------------------------
log_info "Step 2: Backing up QLever indexes..."

QLEVER_DIR=""
POSSIBLE_QLEVER_DIRS=(
    "${ROOT_DIR}/qlever-tests/volumes/cdif/server"
    "${ROOT_DIR}/qlever-tests/volumes/asknowmuseum/server"
    "${ROOT_DIR}/qlever-tests/volumes/codata/server"
)

for dir in "${POSSIBLE_QLEVER_DIRS[@]}"; do
    if [ -f "${dir}/Qleverfile" ] && compgen -G "${dir}/*.index*" > /dev/null; then
        QLEVER_DIR="$(readlink -f "${dir}")"
        break
    fi
done

if [ -z "${QLEVER_DIR}" ] || [ ! -d "${QLEVER_DIR}" ]; then
    log_error "Could not locate QLever index directory containing index files."
    exit 1
fi

QLEVER_ARCHIVE="${DUMPS_DIR}/qlever-cdif-indexes.tar.gz"
log_info "Source directory: ${QLEVER_DIR}"
log_info "Writing archive: ${QLEVER_ARCHIVE} (excluding raw .nt dumps and temp data)..."

tar -C "${QLEVER_DIR}" \
    --exclude="*.nt" \
    --exclude="*.unsorted-triples.dat" \
    --exclude="*.tar.gz" \
    --warning=no-file-changed \
    --use-compress-program="${COMPRESS_CMD}" \
    -cf "${QLEVER_ARCHIVE}" . || [ $? -eq 1 ]

QLEVER_SIZE="$(du -h "${QLEVER_ARCHIVE}" | awk '{print $1}')"
log_success "QLever backup complete: ${QLEVER_ARCHIVE} (${QLEVER_SIZE})"
echo ""

# ------------------------------------------------------------------------------
# Summary & Status
# ------------------------------------------------------------------------------
echo "=================================================================="
log_success "Backup process finished successfully!"
echo "Archives created in ${DUMPS_DIR}:"
ls -lh "${ES_ARCHIVE}" "${QLEVER_ARCHIVE}"
echo "=================================================================="

