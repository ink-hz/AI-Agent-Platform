#!/bin/sh
# Run only in the documented 2-CPU/3-GiB build container; /build is disposable.
set -eu
mode=${1:-build}
case "$mode" in build|--prepare-deps) ;; *) exit 2 ;; esac
export DEBIAN_FRONTEND=noninteractive
test "$(uname -m)" = x86_64
cd /build
printf '%s\n' 'dfedb39ecb7b6d51f11af460c1c8bacdff8b19a16aa9f6e4f00bf5827c93b958  sources/php-src-1d7dec178a094e6e75b229ce22754045f8c6070a.tar.gz' | sha256sum -c -
# Bullseye has been archived. Active compatibility URLs still serve a signed
# index but referenced security packages are gone; use Debian's actual archive.
printf '%s\n' \
    'deb https://archive.debian.org/debian bullseye main' \
    'deb https://archive.debian.org/debian-security bullseye-security main' \
    > /etc/apt/sources.list
gpgv --keyring /usr/share/keyrings/debian-archive-keyring.gpg /build/index-main.InRelease
gpgv --keyring /usr/share/keyrings/debian-archive-keyring.gpg /build/index-security.InRelease
apt-get -o Acquire::https::Timeout=30 -o Acquire::Retries=0 update
sha256sum -c /build/php-build-indexes.sha256
cp /build/php-build-indexes.sha256 /build/artifacts/build-indexes.sha256
test -z "$(grep -Ev '^[a-z0-9][a-z0-9+.-]*$' /build/php-build-packages.txt)"
set -- $(cat /build/php-build-packages.txt)
if [ "$mode" = --prepare-deps ]; then
    apt-get --print-uris -y --no-install-recommends install "$@" > /build/apt-download-plan.txt
    for name in main security; do
        prefix=archive.debian.org_debian_dists_bullseye
        if [ "$name" = security ]; then prefix=archive.debian.org_debian-security_dists_bullseye-security; fi
        # APT may keep a plain, xz or lz4 index. Require one exact producer,
        # and use APT itself to decode it instead of guessing compression.
        set -- /var/lib/apt/lists/"${prefix}"_main_binary-amd64_Packages*
        test "$#" -eq 1
        case "$1" in *_Packages|*_Packages.xz|*_Packages.lz4) ;; *) exit 1 ;; esac
        /usr/lib/apt/apt-helper cat-file "$1" > "/build/apt-$name.Packages"
    done
    printf '%s\n' APT_DOWNLOAD_PLAN_PREPARED
    exit 0
fi
# Copy only after update: Debian Docker images commonly clean archives from an
# APT update hook. Every cache byte was already checked against the signed
# index; normal APT acquisition verifies it again before installation.
test -d /build/deb-cache
cp /build/deb-cache/*.deb /var/cache/apt/archives/
apt-get --print-uris -y --no-install-recommends install "$@" > /build/artifacts/apt-cache-plan.txt
if grep -q "^'https:" /build/artifacts/apt-cache-plan.txt; then
    printf '%s\n' APT_CACHE_INCOMPLETE >&2
    exit 1
fi
apt-get -o Acquire::Retries=0 install -y --no-install-recommends "$@"
dpkg-query -W > /build/artifacts/build-packages.txt
python3 /build/php_build_preflight.py /build > /build/artifacts/build-preflight.json
tar -xzf sources/php-src-1d7dec178a094e6e75b229ce22754045f8c6070a.tar.gz -C work
cd work/php-src-1d7dec178a094e6e75b229ce22754045f8c6070a
./buildconf --force
# Preserve the baseline's built-in extensions and non-thread-safe ABI. The
# builder's Apache headers generate a module for the stable Apache 2.4 API;
# acceptance must still load it in the actual bundled Apache 2.4.62 executable.
set -- $(cat /build/php-configure-args.txt)
./configure "$@"
python3 /build/php_build_preflight.py /build --generated > /build/artifacts/generated-nts-and-mpm.json
make -j2
cp sapi/cli/php /build/artifacts/php
cp libs/libphp.so /build/artifacts/libphp.so
cp modules/opcache.so /build/artifacts/opcache.so
# Bundle non-glibc dependencies in a separate directory. Do not overwrite the
# vendor's /opt/zbox/lib or copy a second dynamic loader into the runtime.
mkdir -p /build/artifacts/lib
ldd sapi/cli/php libs/libphp.so modules/opcache.so > /build/artifacts/ldd-build.txt
python3 - <<'PY'
import pathlib,re,shutil
text=pathlib.Path('/build/artifacts/ldd-build.txt').read_text()
for path in set(re.findall(r'=> (/[^ ]+)', text)):
    name=pathlib.Path(path).name
    if re.match(r'lib(c|m|dl|rt|pthread|resolv|util|anl)\.so\.',name):
        continue
    shutil.copy2(path, pathlib.Path('/build/artifacts/lib')/name, follow_symlinks=True)
PY
./sapi/cli/php -n -v
# No production DB/app is started. A failing exact test prevents a candidate
# receipt and stops immediately, leaving its .out/.diff and per-case log.
/bin/sh /build/test-php-runtime.sh
printf '%s\n' PHP_BUILD_COMPLETE
