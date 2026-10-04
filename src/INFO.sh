#!/bin/bash

set -e

PKG_VERSION="${1:?Package version is required}"
ARCH="${2:?Architecture is required}"
PKG_SIZE="${3:?Package size is required}"

if ! [[ "${PKG_VERSION}" =~ ^[0-9]+(\.[0-9]+)*(-[0-9]+)?$ ]]; then
    echo "ERROR: Invalid package version '${PKG_VERSION}'." >&2
    echo "Synology requires a numeric version such as 2.145.1-70 (letters are not allowed)." >&2
    exit 1
fi

TIMESTAMP="$(date -u +%Y%m%d-%H:%M:%S)"

OS_MIN_VER="7.0-41890"

case "${ARCH}" in

    amd64)
        PLATFORMS="x86_64 apollolake avoton braswell broadwell broadwellnk broadwellnkv2 broadwellntbap bromolow cedarview denverton dockerx64 epyc7002 epyc7003 epyc7003ntb geminilake geminilakenk grantley icelaked kvmx64 purley r1000 r1000nk v1000 v1000nk"
        ;;

    *)
        echo "ERROR: Unsupported architecture: ${ARCH}" >&2
        echo "Supported architectures: amd64" >&2
        exit 1
        ;;

esac

cat <<EOF
package="TorrServer"
version="${PKG_VERSION}"
displayname="TorrServer DSM"
dsmappname="SYNO.SDS.TorrServer.Application"
arch="${PLATFORMS}"
os_min_ver="${OS_MIN_VER}"
dsmuidir="ui"
instuninst_restart_services="nginx.service"
startable="yes"
maintainer="TorrServer"
maintainer_url="https://github.com/YouROK/TorrServer"
distributor="Alex-Goodwin1"
distributor_url="https://github.com/Alex-Goodwin1/TorrServer-DSM"
description="TorrServer, torrent to http (Uncensored build)"
package_icon="PACKAGE_ICON.PNG"
package_icon_256="PACKAGE_ICON_256.PNG"
create_time="${TIMESTAMP}"
extractsize=${PKG_SIZE}
EOF
