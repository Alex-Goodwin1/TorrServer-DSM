TORRSERVER_VERSION := MatriX.147.2

PKG_VERSION := 2.147.2-94

ARCHES := amd64

.PHONY: all clean

all: $(addprefix torrserver-,$(ARCHES))

torrserver-%:
	@./build-package.sh "$(TORRSERVER_VERSION)" "$*" "$(PKG_VERSION)" "$(DSM)"

clean:
	rm -rf spk dest_bin build
