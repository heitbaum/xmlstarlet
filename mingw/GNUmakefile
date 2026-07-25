# Automate building xmlstarlet and its dependencies (downloading them) with a
# mingw-w64 cross compiler, producing a self-contained, statically linked
# xml.exe plus the documentation, packaged as
# xmlstarlet-<version>-windows-<arch>.zip.
#
# The target architecture is selected with ARCH:
#   make               # x86_64 (default)
#   make ARCH=x86_64   # x86_64-w64-mingw32   (Debian/Ubuntu: gcc-mingw-w64-x86-64)
#   make ARCH=arm64    # aarch64-w64-mingw32  (an llvm-mingw toolchain on PATH)
# Each architecture builds into its own build-<arch>/ tree, so the two never
# collide; the release workflow invokes this driver once per architecture.
#
# Also needs the host documentation toolchain: xsltproc, docbook-xsl-ns,
# docbook-xml, fop, ghostscript.

include versions.mk

ARCH ?= x86_64
HOST_x86_64 := x86_64-w64-mingw32
HOST_arm64  := aarch64-w64-mingw32
HOST := $(HOST_$(ARCH))
ifeq ($(HOST),)
$(error unknown ARCH '$(ARCH)'; use x86_64 or arm64)
endif

# Per-architecture tree: extracted dependency sources and their static install
# prefix. Tarballs are shared - downloaded once into this directory.
PREFIX := $(CURDIR)/build-$(ARCH)

FETCH := curl -fSL -o

CONFIGURE_FLAGS := --prefix=$(PREFIX) --host=$(HOST) \
--with-gnu-ld --enable-silent-rules \
CPPFLAGS=-I$(PREFIX)/include \
LDFLAGS=-L$(PREFIX)/lib \
CFLAGS='-O2'

.DEFAULT_GOAL := xmlstarlet

# Remove a target whose recipe failed - so a mismatched checksum drops the bad
# tarball and the next run re-downloads it.
.DELETE_ON_ERROR:

####################
# ZLIB
####################

ZLIB := $(PREFIX)/lib/libz.a

zlib-$(ZLIB_VERSION).tar.xz :
	$(FETCH) $@ \
 'https://github.com/madler/zlib/releases/download/v$(ZLIB_VERSION)/zlib-$(ZLIB_VERSION).tar.xz'
	echo '$(ZLIB_SHA256)  $@' | sha256sum -c -

$(PREFIX)/zlib-$(ZLIB_VERSION) : zlib-$(ZLIB_VERSION).tar.xz
	mkdir -p $(PREFIX)
	tar -xf $< -C $(PREFIX)
	touch $@

$(ZLIB) : $(PREFIX)/zlib-$(ZLIB_VERSION)
	cd $< \
 && make -f win32/Makefile.gcc \
 PREFIX=$(HOST)- \
 CFLAGS='-Os' \
 prefix=$(PREFIX) \
 INCLUDE_PATH=$(PREFIX)/include \
 LIBRARY_PATH=$(PREFIX)/lib \
 BINARY_PATH=$(PREFIX)/bin \
 INSTALL='install -p' \
 install \
 && touch $@

zlib : $(ZLIB)
.PHONY : zlib

####################
# ICONV
####################

ICONV := $(PREFIX)/lib/libiconv.a

libiconv-$(ICONV_VERSION).tar.gz :
	$(FETCH) $@ \
 'https://ftp.gnu.org/gnu/libiconv/libiconv-$(ICONV_VERSION).tar.gz'
	echo '$(ICONV_SHA256)  $@' | sha256sum -c -

$(PREFIX)/libiconv-$(ICONV_VERSION) : libiconv-$(ICONV_VERSION).tar.gz
	mkdir -p $(PREFIX)
	tar -xf $< -C $(PREFIX)
	touch $@

$(ICONV) : $(PREFIX)/libiconv-$(ICONV_VERSION)
	cd $< \
 && ./configure $(CONFIGURE_FLAGS) --enable-static --disable-shared \
 && make install \
 && touch $@

iconv : $(ICONV)
.PHONY : iconv

####################
# LIBXML
####################

LIBXML := $(PREFIX)/lib/libxml2.a

libxml2-$(XML2_VERSION).tar.xz :
	$(FETCH) $@ \
 'https://download.gnome.org/sources/libxml2/$(basename $(XML2_VERSION))/libxml2-$(XML2_VERSION).tar.xz'
	echo '$(XML2_SHA256)  $@' | sha256sum -c -

$(PREFIX)/libxml2-$(XML2_VERSION) : libxml2-$(XML2_VERSION).tar.xz
	mkdir -p $(PREFIX)
	tar -xf $< -C $(PREFIX)
	touch $@

$(LIBXML) : $(PREFIX)/libxml2-$(XML2_VERSION) $(ICONV) $(ZLIB)
	cd $< \
 && ./configure $(CONFIGURE_FLAGS) \
    --disable-shared \
    --without-debug \
    --without-legacy \
    --without-python \
    --without-readline \
    --without-sax1 \
    --without-threads \
    --without-modules \
    --without-icu \
    --with-zlib=$(PREFIX) \
    --with-iconv=$(PREFIX) \
 && make install \
 && touch $@

libxml2 : $(LIBXML)
.PHONY : libxml2

####################
# LIBXSLT
####################

LIBXSLT := $(PREFIX)/lib/libxslt.a

libxslt-$(XSLT_VERSION).tar.xz :
	$(FETCH) $@ \
 'https://download.gnome.org/sources/libxslt/$(basename $(XSLT_VERSION))/libxslt-$(XSLT_VERSION).tar.xz'
	echo '$(XSLT_SHA256)  $@' | sha256sum -c -

$(PREFIX)/libxslt-$(XSLT_VERSION) : libxslt-$(XSLT_VERSION).tar.xz
	mkdir -p $(PREFIX)
	tar -xf $< -C $(PREFIX)
	touch $@

$(LIBXSLT) : $(PREFIX)/libxslt-$(XSLT_VERSION) $(LIBXML)
	cd $< \
 && ./configure $(CONFIGURE_FLAGS) \
    --with-libxml-prefix=$(PREFIX) \
    --disable-shared \
    --without-plugins \
    --without-debugger \
    --without-python \
    --without-crypto \
 && make install \
 && touch $@

libxslt : $(LIBXSLT)
.PHONY : libxslt

####################
# XMLSTARLET
####################

BUILDDIR := xmlstarlet-$(XMLSTARLET_VERSION)-$(ARCH)
ZIP      := xmlstarlet-$(XMLSTARLET_VERSION)-windows-$(ARCH).zip

# cross-compilation cannot run the malloc/realloc(0) probes; seed their cache
export ac_cv_func_realloc_0_nonnull := yes
export ac_cv_func_malloc_0_nonnull := yes

$(BUILDDIR)/$(ZIP) : $(LIBXML) $(LIBXSLT)
	rm -rf $(BUILDDIR) \
 && mkdir $(BUILDDIR) \
 && cd $(BUILDDIR) \
 && ../../configure $(CONFIGURE_FLAGS) \
    --with-libxml-prefix=$(PREFIX) \
    --with-libxslt-prefix=$(PREFIX) \
    --enable-static-libs \
    --enable-build-docs \
 && mkdir -p doc \
 && make dist-windows WINARCH=$(ARCH) STRIP=$(HOST)-strip

xmlstarlet : $(BUILDDIR)/$(ZIP)
.PHONY : xmlstarlet

clean :
	rm -rf $(BUILDDIR)

distclean :
	rm -rf build-* xmlstarlet-*-x86_64 xmlstarlet-*-arm64 *.tar.[gx]z
