#!/bin/sh
# DNS-only dispatcher. The independent Certbot 5.8 IP service remains responsible for sdk-ip.
set -u

if [ "$#" -ne 0 ]; then
    printf '%s\n' '{"status":"error","code":"unexpected_arguments"}'
    exit 2
fi

# Explicit dependency override supports isolated behavior tests; production uses /usr/bin/certbot.
certbot_binary=${CERTBOT_BINARY:-/usr/bin/certbot}
if [ ! -x "$certbot_binary" ]; then
    printf '%s\n' '{"status":"error","code":"certbot_unavailable"}'
    exit 2
fi

renewal_exit=0
for lineage in agent.orbbec.com.cn fae.orbbec.com.cn; do
    if "$certbot_binary" -q renew --cert-name "$lineage" --no-random-sleep-on-renew >/dev/null 2>&1; then
        printf '{"lineage":"%s","status":"ok"}\n' "$lineage"
    else
        lineage_exit=$?
        printf '{"lineage":"%s","status":"error","exit_code":%s}\n' "$lineage" "$lineage_exit"
        renewal_exit=1
    fi
done
exit "$renewal_exit"
