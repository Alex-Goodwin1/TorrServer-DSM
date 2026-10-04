TORRSERVER_VERSION := MatriX.145.1

# Synology Package Center requires a strictly NUMERIC package version:
#   <major>.<minor>.<build>[-<revision>]
# A version containing letters (e.g. the old "1.4.145.UN.70") is rejected by
# DSM with "Invalid file format" / "Неверный формат файла" before anything else
# runs, so the "Uncensored" marker must live in displayname/description instead.
# The leading 2.x keeps this fork newer than vladlenas' 1.9.x builds, so Package
# Center offers it as an upgrade.
PKG_VERSION := 2.145.1-71

ARCHES := amd64 arm64 arm7

.PHONY: all clean

all: $(addprefix torrserver-,$(ARCHES))

torrserver-%:
	@./build-package.sh "$(TORRSERVER_VERSION)" "$*" "$(PKG_VERSION)" "$(DSM)"

clean:
	rm -rf spk dest_bin build
