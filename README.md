# TorrServer DSM

Synology DSM package for [TorrServer](https://github.com/YouROK/TorrServer).

TorrServer DSM provides a native DSM interface for managing TorrServer on Synology NAS.

## Features

- DSM interface for package management

### Settings

- Port configuration
- HTTP / HTTPS
- Force HTTPS
- HTTP authentication
- TorrServer cache configuration
- Logs and package status
- Restart TorrServer from DSM

## Requirements

- Synology DSM 7.3 or newer
- Supported architectures:
  - `amd64`
  - `arm64`
  - `arm7`

## Installation

Install the `.spk` package through:

**Package Center → Manual Install**

After installation, open **TorrServer DSM** from the DSM desktop.

## Building

Clone the repository:

    git clone https://github.com/vladlenas/TorrServer-DSM.git
    cd TorrServer-DSM

Build the SPK packages:

    make

The generated packages are placed in the `build` directory.

To clean the build directory:

    make clean

## TorrServer Version

The TorrServer version used by the package is defined in `Makefile`:

    TORRSERVER_VERSION := MatriX.145

The package version is also defined in `Makefile`:

    PKG_VERSION := 1.2.145

## Links

- [TorrServer Project](https://github.com/YouROK/TorrServer)
- [TorrServer DSM](https://github.com/vladlenas/TorrServer-DSM)
- [Support TorrServer Project](https://github.com/YouROK/TorrServer#donate)

## Credits

TorrServer by [YouROK](https://github.com/YouROK/TorrServer).

Synology SPK package maintained by [vladlenas](https://github.com/vladlenas).
