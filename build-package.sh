#!/bin/bash
set -e

TORRSERVER_VERSION=$1
ARCH=$2
PKG_VERSION=$3

# Pinned SHA-256 sums of everything that is downloaded (one "<hash>  <key>" per
# line). Without a pin a download is only trusted on first use:
#   UPDATE_CHECKSUMS=1   pin sums that are not recorded yet (see `make checksums`)
#   REQUIRE_CHECKSUMS=1  refuse downloads that have no pinned sum
#   DOWNLOAD_ONLY=1      stop after downloading
CHECKSUM_FILE="${CHECKSUM_FILE:-checksums.sha256}"

sha256_of() {
    sha256sum "$1" | awk '{print $1}'
}

verify_checksum() {
    local file=$1
    local key=$2
    local actual expected

    actual="$(sha256_of "${file}")"
    expected=""

    if [[ -f ${CHECKSUM_FILE} ]]; then
        expected="$(awk -v k="${key}" '$2 == k {print $1; exit}' "${CHECKSUM_FILE}")"
    fi

    if [[ -n ${expected} ]]; then
        if [[ ${expected} != "${actual}" ]]; then
            rm -f "${file}"
            echo "ERROR: checksum mismatch for ${key}" >&2
            echo "  expected ${expected}" >&2
            echo "  actual   ${actual}" >&2
            exit 1
        fi
        echo ">>> Checksum OK: ${key}"
    elif [[ ${UPDATE_CHECKSUMS:-0} == 1 ]]; then
        echo "${actual}  ${key}" >> "${CHECKSUM_FILE}"
        echo ">>> Pinned ${key}"
    elif [[ ${REQUIRE_CHECKSUMS:-0} == 1 ]]; then
        rm -f "${file}"
        echo "ERROR: no pinned checksum for ${key} (run: make checksums)" >&2
        exit 1
    else
        echo ">>> WARNING: no pinned checksum for ${key} (${actual})" >&2
    fi
}

# Downloads are cached per version and written to a temporary file first, so a
# changed TORRSERVER_VERSION is fetched again and an interrupted download is
# never mistaken for a finished one.
download_file() {
    local url=$1
    local dest=$2
    local part="${dest}.part"

    rm -f "${part}"
    mkdir -p "$(dirname "${dest}")"

    if ! wget -q -O "${part}" "${url}"; then
        rm -f "${part}"
        echo "ERROR: download failed: ${url}" >&2
        exit 1
    fi

    if [[ ! -s ${part} ]]; then
        rm -f "${part}"
        echo "ERROR: empty download: ${url}" >&2
        exit 1
    fi

    mv -f "${part}" "${dest}"
}

torrserver_bin_path() {
    echo "dest_bin/${TORRSERVER_VERSION}/TorrServer-linux-${ARCH}"
}

download_torrserver() {
    local base_url="https://github.com/YouROK/TorrServer/releases/download/${TORRSERVER_VERSION}"
    local bin_name="TorrServer-linux-${ARCH}"
    local dest_bin
    dest_bin="$(torrserver_bin_path)"

    if [[ -s ${dest_bin} ]]; then
        echo ">>> Binaries already exist: ${bin_name} (${TORRSERVER_VERSION})"
        verify_checksum "${dest_bin}" "TorrServer-${TORRSERVER_VERSION}-linux-${ARCH}"
        return
    fi

    echo ">>> Downloading ${bin_name} ${TORRSERVER_VERSION}:"
    download_file "${base_url}/${bin_name}" "${dest_bin}"

    # Guard against saving an HTML error page as the binary.
    if [[ "$(head -c 4 "${dest_bin}" | od -An -c | tr -d ' ')" != '177ELF' ]]; then
        rm -f "${dest_bin}"
        echo "ERROR: ${bin_name} is not an ELF binary" >&2
        exit 1
    fi

    verify_checksum "${dest_bin}" "TorrServer-${TORRSERVER_VERSION}-linux-${ARCH}"
}

download_ffprobe() {
    local dest_bin="dest_bin"
    local tmp_dir="./build/ffprobe-${ARCH}"
    local ffprobe_bin="${dest_bin}/ffprobe-${ARCH}"
    local ffprobe_url=""

    case "${ARCH}" in
        amd64)
            ffprobe_url="https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/v6.1/ffprobe-6.1-linux-64.zip"
            ;;
        arm64)
            ffprobe_url="https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/v6.1/ffprobe-6.1-linux-arm-64.zip"
            ;;
        arm7)
            ffprobe_url="https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/v6.1/ffprobe-6.1-linux-armhf-32.zip"
            ;;
        *)
            echo "ERROR: Unsupported architecture for ffprobe: ${ARCH}" >&2
            exit 1
            ;;
    esac

    if [[ -f ${ffprobe_bin} ]]; then
        echo ">>> Binaries already exist: ffprobe-${ARCH}"
        return
    fi

    echo ">>> Downloading ffprobe for ${ARCH}:"

    mkdir -p "${dest_bin}" "${tmp_dir}"

    download_file "${ffprobe_url}" "${tmp_dir}/ffprobe.zip"
    # Verify the archive before it is unpacked.
    verify_checksum "${tmp_dir}/ffprobe.zip" "$(basename "${ffprobe_url}")"
    unzip -q "${tmp_dir}/ffprobe.zip" -d "${tmp_dir}"

    mv "${tmp_dir}/ffprobe" "${ffprobe_bin}"
    chmod +x "${ffprobe_bin}"

    rm -rf "${tmp_dir}"
}

make_inner_pkg() {
    local tmp_dir=$1
    local dest_dir=$2
    local dest_pkg="$dest_dir/package.tgz"
    local torrserver_bin
    torrserver_bin="$(torrserver_bin_path)"
    local ffprobe_bin="dest_bin/ffprobe-${ARCH}"

    echo ">>> Making inner package.tgz"

    mkdir -p "${tmp_dir}/bin"

    cp -a "${torrserver_bin}" "${tmp_dir}/bin/TorrServer"
    cp -a "${ffprobe_bin}" "${tmp_dir}/bin/ffprobe"

    chmod +x "${tmp_dir}"/bin/*

    cp -r src/ui "${tmp_dir}"
    cp -r src/nginx "${tmp_dir}"
    rm -rf "${tmp_dir}/helper"
    cp -a src/helper "${tmp_dir}/helper"
    rm -rf "${tmp_dir}/helper"/__pycache__
    chmod +x "${tmp_dir}/helper/helper.py"

    pkg_size=$(du -sk "${tmp_dir}" | awk '{print $1}')
    echo "${pkg_size}" >>"$dest_dir/extractsize_tmp"

    find "${tmp_dir}" -mindepth 1 -maxdepth 1 -printf '%f\n' | tar -cJf "${dest_pkg}" -C "${tmp_dir}" -T /dev/stdin
}

make_spk() {
    local spk_tmp_dir=$1
    local spk_dest_dir="./spk"
    local pkg_size
    pkg_size=$(cat "${spk_tmp_dir}/extractsize_tmp")
    local spk_filename="TorrServer-DSM-${TORRSERVER_VERSION}-${ARCH}.spk"

    echo ">>> Making spk: ${spk_filename}"

    mkdir -p "${spk_dest_dir}"
    rm -f "${spk_tmp_dir}/extractsize_tmp"

    cp -r src/scripts "${spk_tmp_dir}"
    cp -r src/PACKAGE_ICON_256.PNG "${spk_tmp_dir}"
    cp -r src/PACKAGE_ICON.PNG "${spk_tmp_dir}"
    cp -r src/conf/ "${spk_tmp_dir}"
    cp -r src/WIZARD_UIFILES "${spk_tmp_dir}"

    ./src/INFO.sh "${PKG_VERSION}" "${ARCH}" "${pkg_size}" >"${spk_tmp_dir}/INFO"

    find "${spk_tmp_dir}" -mindepth 1 -maxdepth 1 -printf '%f\n' | tar -cf "${spk_dest_dir}/${spk_filename}" -C "${spk_tmp_dir}" -T /dev/stdin
}

make_pkg() {
    mkdir -p ./build

    local pkg_temp_dir spk_temp_dir
    pkg_temp_dir=$(mktemp -d -p ./build)
    spk_temp_dir=$(mktemp -d -p ./build)

    make_inner_pkg ${pkg_temp_dir} ${spk_temp_dir}
    make_spk ${spk_temp_dir}

    echo ">>> Done"
    echo ""
}

main() {
    echo ">>> Building package for ${TORRSERVER_VERSION} ${ARCH}"

    download_ffprobe
    download_torrserver

    if [[ ${DOWNLOAD_ONLY:-0} == 1 ]]; then
        echo ">>> Download only, skipping package build"
        return
    fi

    make_pkg

    echo ">>> Done"
}

main
