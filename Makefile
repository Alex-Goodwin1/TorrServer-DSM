TORRSERVER_VERSION := MatriX.147.1

PKG_VERSION := 2.147.1-92

ARCHES := amd64

.PHONY: all clean

all: $(addprefix torrserver-,$(ARCHES))

torrserver-%:
	@./build-package.sh "$(TORRSERVER_VERSION)" "$*" "$(PKG_VERSION)" "$(DSM)"

clean:
	rm -rf spk dest_bin build
