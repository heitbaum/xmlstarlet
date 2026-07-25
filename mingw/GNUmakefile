# Automate building xmlstarlet and its dependencies (downloading them) with the
# mingw-w64 cross compiler, producing a self-contained, statically linked
# 64-bit xml.exe plus the documentation, packaged as
# xmlstarlet-<version>-win32.zip by `make dist-win32`.
#
# Run locally:  cd mingw && make
#   needs:  x86_64-w64-mingw32-gcc  (Debian/Ubuntu: gcc-mingw-w64-x86-64)
#           and the host doc toolchain the docs need: xsltproc,
#           docbook-xsl-ns, docbook-xml, fop, ghostscript.
# The release workflow invokes this same driver.

include versions.mk

PREFIX := $(CURDIR)/build
HOST   := x86_64-w64-mingw32

FETCH := curl -fSL -o

CONFIGURE_FLAGS := --prefix=$(PREFIX) --host=$(HOST) \
--with-gnu-ld --enable-silent-rules \
CPPFLAGS=-I$(PREFIX)/include \
LDFLAGS=-L$(PREFIX)/lib \
CFLAGS='-O2'

.DEFAULT_GOAL := xmlstarlet

####################
# ZLIB
####################

ZLIB := $(PREFIX)/lib/libz.a

zlib-$(ZLIB_VERSION).tar.xz :
	$(FETCH) $@ \
 'https://github.com/madler/zlib/releases/download/v$(ZLIB_VERSION)/zlib-$(ZLIB_VERSION).tar.xz'

zlib-$(ZLIB_VERSION) : zlib-$(ZLIB_VERSION).tar.xz
	tar -xf $<
	touch $@

$(ZLIB) : zlib-$(ZLIB_VERSION)
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

libiconv-$(ICONV_VERSION) : libiconv-$(ICONV_VERSION).tar.gz
	tar -xf $<
	touch $@

$(ICONV) : libiconv-$(ICONV_VERSION)
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

libxml2-$(XML2_VERSION) : libxml2-$(XML2_VERSION).tar.xz
	tar -xf $<
	touch $@

$(LIBXML) : libxml2-$(XML2_VERSION) $(ICONV) $(ZLIB)
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

libxslt-$(XSLT_VERSION) : libxslt-$(XSLT_VERSION).tar.xz
	tar -xf $<
	touch $@

$(LIBXSLT) : libxslt-$(XSLT_VERSION) $(LIBXML)
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

xmlstarlet-$(XMLSTARLET_VERSION) :
	mkdir $@

# cross-compilation cannot run the malloc/realloc(0) probes; seed their cache
export ac_cv_func_realloc_0_nonnull := yes
export ac_cv_func_malloc_0_nonnull := yes

xmlstarlet-$(XMLSTARLET_VERSION)/xmlstarlet-$(XMLSTARLET_VERSION)-win32.zip : xmlstarlet-$(XMLSTARLET_VERSION) $(LIBXML) $(LIBXSLT)
	rm -rf xmlstarlet-$(XMLSTARLET_VERSION) \
 && mkdir xmlstarlet-$(XMLSTARLET_VERSION) \
 && cd xmlstarlet-$(XMLSTARLET_VERSION) \
 && ../../configure $(CONFIGURE_FLAGS) \
    --with-libxml-prefix=$(PREFIX) \
    --with-libxslt-prefix=$(PREFIX) \
    --enable-static-libs \
    --enable-build-docs \
 && make dist-win32 \
 && cd .. \
 && touch $@

xmlstarlet : xmlstarlet-$(XMLSTARLET_VERSION)/xmlstarlet-$(XMLSTARLET_VERSION)-win32.zip
.PHONY : xmlstarlet

clean :
	rm -rf xmlstarlet-$(XMLSTARLET_VERSION)

distclean : clean
	rm -rf */ *.tar.[gx]z
