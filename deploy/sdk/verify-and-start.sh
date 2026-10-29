#!/bin/sh
set -eu

sha256sum -c /usr/local/share/sdk-php/runtime.sha256 >/dev/null
/opt/zbox/bin/php /usr/local/share/sdk-php/check-php-runtime.php >/dev/null
printf '%s\n' '9c6710dab686d7c39f8e3e49f9ea91ab575fc51c384bd9dd3d19ac12eb883b88  /apps/zentao/module/action/control.php' | sha256sum -c - >/dev/null
if [ "${1:-}" = "--verify-only" ]; then
    printf '%s\n' PHP_RUNTIME_GUARD_PASSED
    exit 0
fi
exec /usr/bin/entrypoint.sh "$@"
