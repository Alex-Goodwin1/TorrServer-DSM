TORRSERVER_VERSION := MatriX.145.1

PKG_VERSION := 2.145.1-72

ARCHES := amd64

.PHONY: all clean

all: $(addprefix torrserver-,$(ARCHES))

torrserver-%:
	@./build-package.sh "$(TORRSERVER_VERSION)" "$*" "$(PKG_VERSION)" "$(DSM)"

clean:
	rm -rf spk dest_bin build
