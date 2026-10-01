TORRSERVER_VERSION := MatriX.145.1
PKG_VERSION := 1.4.145.UN.70

ARCHES := amd64 arm64 arm7

.PHONY: all clean

all: $(addprefix torrserver-,$(ARCHES))

torrserver-%:
	@./build-package.sh "$(TORRSERVER_VERSION)" "$*" "$(PKG_VERSION)" "$(DSM)"

clean:
	rm -rf spk dest_bin build
