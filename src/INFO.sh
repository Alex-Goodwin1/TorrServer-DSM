#!/bin/bash

set -e

PKG_VERSION="${1:?Package version is required}"
ARCH="${2:?Architecture is required}"
PKG_SIZE="${3:?Package size is required}"

TIMESTAMP="$(date -u +%Y%m%d-%H:%M:%S)"

# Modified to support DSM 7.0 and newer.
OS_MIN_VER="7.0-41890"

case "${ARCH}" in

    amd64)
        PLATFORMS="x86_64 apollolake avoton braswell broadwell broadwellnk broadwellnkv2 broadwellntbap bromolow denverton epyc7002 geminilake grantley kvmx64 purley r1000 v1000 v1000nk r1000nk geminilakenk icelaked epyc7003"
        ;;

    arm64)
        PLATFORMS="aarch64 armv8 rtd1296 rtd1619b armada37xx"
        ;;

    arm7)
        PLATFORMS="armv7 alpine alpine4k armada370 armada375 armada38x armadaxp monaco"
        ;;

    *)
        echo "ERROR: Unsupported architecture: ${ARCH}" >&2
        echo "Supported architectures: amd64, arm64, arm7" >&2
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
