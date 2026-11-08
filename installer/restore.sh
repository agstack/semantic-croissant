#!/usr/bin/env bash
# ==============================================================================
# Semantic Croissant - Restore Script for Elasticsearch, QLever and MinIO
#
# Usage:
#   ./installer/restore.sh [profile_name] [dumps_dir]
#
# Defaults:
#   profile_name: asknowmuseum (or $PROFILE_NAME)
#   dumps_dir:    ./dumps
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PROFILE_NAME="${1:-${PROFILE_NAME:-asknowmuseum}}"
DUMPS_DIR="${2:-${ROOT_DIR}/dumps}"

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

if command -v pigz >/dev/null 2>&1; then
    DECOMPRESS_CMD="pigz -dc"
else
    DECOMPRESS_CMD="gzip -dc"
fi

echo "=================================================================="
echo "   Semantic Croissant - Database & Knowledge Graph Restore"
echo "=================================================================="
log_info "Target profile: ${PROFILE_NAME}"
log_info "Source dumps directory: ${DUMPS_DIR}"

ES_TARGET="${ROOT_DIR}/qlever-tests/volumes/${PROFILE_NAME}/es"
QLEVER_TARGET="${ROOT_DIR}/qlever-tests/volumes/${PROFILE_NAME}/server"
MINIO_TARGET="${ROOT_DIR}/volumes/${PROFILE_NAME}/minio"
ES_VOLUMES_LINK="${ROOT_DIR}/volumes/${PROFILE_NAME}/es"

mkdir -p "${ROOT_DIR}/volumes/${PROFILE_NAME}"
mkdir -p "${ROOT_DIR}/qlever-tests/volumes/${PROFILE_NAME}"
mkdir -p "${ES_TARGET}" "${QLEVER_TARGET}" "${MINIO_TARGET}"

# Ensure destination directories are writable before unpacking
if docker info >/dev/null 2>&1; then
    docker run --rm \
        -v "${ROOT_DIR}/qlever-tests/volumes/${PROFILE_NAME}:/qlever_vol" \
        -v "${ROOT_DIR}/volumes/${PROFILE_NAME}:/root_vol" \
        busybox sh -c "chmod -R 777 /qlever_vol /root_vol 2>/dev/null || true" 2>/dev/null || true
fi

# Ensure volumes/ link exists if compose mounts ./volumes/<profile>/es
if [ ! -e "${ES_VOLUMES_LINK}" ]; then
    log_info "Creating symlink ${ES_VOLUMES_LINK} -> ${ES_TARGET}..."
    ln -s "${ES_TARGET}" "${ES_VOLUMES_LINK}" 2>/dev/null || true
fi

# ------------------------------------------------------------------------------
# 1. Restore Elasticsearch
# ------------------------------------------------------------------------------
# Support full archive or keyword-specific subset archives in dumps/ or dumps/<kw>/
ES_ARCHIVE="${DUMPS_DIR}/elasticsearch-indexes.tar.gz"
if [ ! -f "${ES_ARCHIVE}" ]; then
    ES_ARCHIVE="$(find "${DUMPS_DIR}" -maxdepth 2 -name "elasticsearch-*.tar.gz" 2>/dev/null | head -n 1 || true)"
fi

if [ -n "${ES_ARCHIVE}" ] && [ -f "${ES_ARCHIVE}" ]; then
    log_info "Restoring Elasticsearch indexes/data from ${ES_ARCHIVE} to ${ES_TARGET}..."
    mkdir -p "${ES_TARGET}"
    # Check if archive contains direct ES data directory (indices/) or JSONL hits
    if tar -ztf "${ES_ARCHIVE}" 2>/dev/null | grep -q "es_hits.jsonl"; then
        log_info "Archive contains Elasticsearch JSONL documents. Unpacking to ${ES_TARGET}..."
        tar --no-same-owner --no-same-permissions --touch -xf "${ES_ARCHIVE}" --use-compress-program="${DECOMPRESS_CMD}" -C "${ES_TARGET}" 2>/dev/null || tar -xf "${ES_ARCHIVE}" -C "${ES_TARGET}" 2>/dev/null || true
        # Also ensure unpacked in volumes/${PROFILE_NAME}/es in case symlink wasn't created yet
        if [ -d "${ES_VOLUMES_LINK}" ] && [ "${ES_VOLUMES_LINK}" != "${ES_TARGET}" ]; then
            cp -f "${ES_TARGET}/es_hits.jsonl" "${ES_VOLUMES_LINK}/" 2>/dev/null || true
        fi
        log_success "Elasticsearch documents unpacked to ${ES_TARGET}."
    else
        tar --no-same-owner --no-same-permissions --touch -xf "${ES_ARCHIVE}" --use-compress-program="${DECOMPRESS_CMD}" -C "${ES_TARGET}" 2>/dev/null || tar -xf "${ES_ARCHIVE}" -C "${ES_TARGET}" 2>/dev/null || true
        log_success "Elasticsearch binary indexes restored to ${ES_TARGET}."
    fi
else
    log_warn "Elasticsearch archive not found in ${DUMPS_DIR} (skipping ES restore)."
fi

# ------------------------------------------------------------------------------
# 2. Restore QLever
# ------------------------------------------------------------------------------
QLEVER_ARCHIVE="${DUMPS_DIR}/qlever-cdif-indexes.tar.gz"
if [ ! -f "${QLEVER_ARCHIVE}" ]; then
    QLEVER_ARCHIVE="$(find "${DUMPS_DIR}" -maxdepth 2 -name "qlever-*.tar.gz" 2>/dev/null | head -n 1 || true)"
fi

if [ -n "${QLEVER_ARCHIVE}" ] && [ -f "${QLEVER_ARCHIVE}" ]; then
    log_info "Restoring QLever indexes from ${QLEVER_ARCHIVE} to ${QLEVER_TARGET}..."
    mkdir -p "${QLEVER_TARGET}"
    tar --no-same-owner --no-same-permissions --touch -xf "${QLEVER_ARCHIVE}" --use-compress-program="${DECOMPRESS_CMD}" -C "${QLEVER_TARGET}" 2>/dev/null || tar -xf "${QLEVER_ARCHIVE}" -C "${QLEVER_TARGET}" 2>/dev/null || true
    log_success "QLever indexes restored to ${QLEVER_TARGET}."
else
    log_warn "QLever archive not found in ${DUMPS_DIR} (skipping QLever restore)."
fi

# ------------------------------------------------------------------------------
# 3. Restore MinIO
# ------------------------------------------------------------------------------
MINIO_ARCHIVE="${DUMPS_DIR}/minio-data.tar.gz"
if [ ! -f "${MINIO_ARCHIVE}" ]; then
    MINIO_ARCHIVE="$(find "${DUMPS_DIR}" -maxdepth 2 -name "minio-*.tar.gz" 2>/dev/null | head -n 1 || true)"
fi

if [ -n "${MINIO_ARCHIVE}" ] && [ -f "${MINIO_ARCHIVE}" ]; then
    log_info "Restoring MinIO data from ${MINIO_ARCHIVE} to ${MINIO_TARGET}..."
    mkdir -p "${MINIO_TARGET}"
    tar --no-same-owner --no-same-permissions --touch -xf "${MINIO_ARCHIVE}" --use-compress-program="${DECOMPRESS_CMD}" -C "${MINIO_TARGET}" 2>/dev/null || tar -xf "${MINIO_ARCHIVE}" -C "${MINIO_TARGET}" 2>/dev/null || true
    log_success "MinIO data restored to ${MINIO_TARGET}."
else
    log_warn "MinIO archive not found in ${DUMPS_DIR} (skipping MinIO restore)."
fi

# Ensure permissions
if docker info >/dev/null 2>&1; then
    log_info "Ensuring correct volume file permissions via docker busybox..."
    mkdir -p "${ES_TARGET}" "${QLEVER_TARGET}" "${MINIO_TARGET}"
    docker run --rm \
        -v "${ES_TARGET}:/data/es" \
        -v "${QLEVER_TARGET}:/data/server" \
        -v "${MINIO_TARGET}:/data/minio" \
        busybox sh -c "chown -R 1000:0 /data/es && chown -R 65534:0 /data/server && chown -R 65532:65532 /data/minio" 2>/dev/null || true
fi

echo "=================================================================="
log_success "Restore completed for profile '${PROFILE_NAME}'!"
echo "=================================================================="
