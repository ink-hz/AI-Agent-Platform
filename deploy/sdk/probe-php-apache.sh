#!/bin/sh
# Isolated, content-free acceptance against the bundled Apache executable.
set -eu
/usr/local/bin/verify-and-start.sh --verify-only
root=$(mktemp -d /tmp/sdk-php-probe.XXXXXX)
chmod 755 "$root"
apache_pid=
stage=apache_start
cleanup() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ]; then
        printf '%s\n' "PHP_APACHE_PROBE_FAILED stage=$stage status=$status" >&2
        cat "$root/error.log" "$root/php-error.log" >&2 2>/dev/null || true
    fi
    if [ -n "$apache_pid" ]; then kill "$apache_pid" 2>/dev/null || true; wait "$apache_pid" 2>/dev/null || true; fi
    rm -rf "$root"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
cp /usr/local/share/sdk-php/check-php-runtime.php "$root/probe.php"
mkdir "$root/sessions"
chown nobody:nogroup "$root/sessions"
chmod 700 "$root/sessions"
touch "$root/php-error.log"
chown nobody:nogroup "$root/php-error.log"
cat > "$root/session-fatal.php" <<'PHP'
<?php
if (ini_get('session.serialize_handler') !== 'php_serialize') { http_response_code(500); exit('initial_handler_invalid'); }
if (ini_set('session.serialize_handler', 'php') !== 'php_serialize'
    || ini_get('session.serialize_handler') !== 'php') { http_response_code(500); exit('handler_switch_invalid'); }
session_start();
$_SESSION['isolated_probe'] = 'no_business_data';
trigger_error('controlled_session_fatal', E_USER_ERROR);
PHP
mkdir "$root/early" "$root/uploads"
chown nobody:nogroup "$root/uploads"
chmod 700 "$root/uploads"
cp "$root/probe.php" "$root/early/probe.php"
cat > "$root/upload.php" <<'PHP'
<?php
$file = $_FILES['probe'] ?? null;
$expected = hash('sha256', "isolated-upload\n");
if (!is_array($file) || $file['error'] !== UPLOAD_ERR_OK || !is_uploaded_file($file['tmp_name'])) { http_response_code(500); exit('upload_invalid'); }
$destination = __DIR__ . '/uploads/probe.txt';
if (!move_uploaded_file($file['tmp_name'], $destination) || hash_file('sha256', $destination) !== $expected) { http_response_code(500); exit('upload_hash_invalid'); }
unlink($destination);
echo json_encode(['status'=>'ok','files'=>1,'bytes'=>$file['size'],'sha256'=>$expected]);
PHP
cat > "$root/httpd.conf" <<EOF
ServerRoot /opt/zbox/run/apache
Listen 127.0.0.1:18089
ServerName localhost
PidFile $root/httpd.pid
ErrorLog $root/error.log
LoadModule mpm_prefork_module modules/mod_mpm_prefork.so
LoadModule unixd_module modules/mod_unixd.so
LoadModule authz_core_module modules/mod_authz_core.so
LoadModule php_module modules/libphp.so
User nobody
Group nogroup
ServerLimit 1
StartServers 1
MinSpareServers 1
MaxSpareServers 1
MaxRequestWorkers 1
MaxConnectionsPerChild 0
PHPIniDir /opt/zbox/etc/php
php_admin_value memory_limit 4M
php_admin_value post_max_size 16M
php_admin_value session.save_path $root/sessions
php_admin_value error_log $root/php-error.log
php_admin_flag display_errors Off
php_admin_flag log_errors On
php_admin_value error_reporting 32767
DocumentRoot $root
<Directory "$root">
    Require all granted
    SetHandler application/x-httpd-php
</Directory>
<Files "session-fatal.php">
    php_value session.serialize_handler php_serialize
</Files>
<Directory "$root/early">
    php_flag auto_detect_line_endings On
</Directory>
EOF
/opt/zbox/run/apache/httpd -DFOREGROUND -f "$root/httpd.conf" &
apache_pid=$!
ready=false
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if /opt/zbox/bin/php -r '$s=@fsockopen("127.0.0.1",18089,$e,$m,0.2);if(!$s)exit(1);fclose($s);' 2>/dev/null; then ready=true; break; fi
    if ! kill -0 "$apache_pid" 2>/dev/null; then break; fi
    sleep 1
done
if [ "$ready" != true ]; then cat "$root/error.log" >&2; exit 1; fi
stage=normal_php
curl --silent --show-error --fail --max-time 5 http://127.0.0.1:18089/probe.php > "$root/response.json"
/opt/zbox/bin/php -r '
$value=json_decode(file_get_contents($argv[1]),true,512,JSON_THROW_ON_ERROR);
if (($value["status"]??null)!=="ok" || ($value["sapi"]??null)!=="apache2handler") exit(1);
echo json_encode($value,JSON_THROW_ON_ERROR),PHP_EOL;
' "$root/response.json"
# Reuse the worker and its OPcache; request teardown must remain healthy.
curl --silent --show-error --fail --max-time 2 http://127.0.0.1:18089/probe.php > "$root/second.json"
cmp "$root/response.json" "$root/second.json"
printf '%s\n' 'isolated-upload' > "$root/upload.txt"
stage=multipart_upload
curl --silent --show-error --fail --max-time 5 -F "probe=@$root/upload.txt;type=text/plain" \
    http://127.0.0.1:18089/upload.php > "$root/upload.json"
/opt/zbox/bin/php -r '$v=json_decode(file_get_contents($argv[1]),true,512,JSON_THROW_ON_ERROR);if(($v["status"]??null)!=="ok"||$v["files"]!==1||$v["bytes"]!==16||$v["sha256"]!==hash("sha256","isolated-upload\n"))exit(1);echo json_encode($v),PHP_EOL;' "$root/upload.json"
worker_pid=$(/opt/zbox/bin/php -r '$v=json_decode(file_get_contents($argv[1]),true);echo $v["worker_pid"];' "$root/response.json")
printf '%s\n' "PHP_PROBE_WAITING_OOM parent_pid=$apache_pid worker_pid=$worker_pid"
stage=wait_exit_observer
test -d /test-support
observed=false
for attempt in $(seq 1 200); do
    if [ -f /test-support/observer-ready ]; then observed=true; break; fi
    sleep 0.1
done
test "$observed" = true
# Eight MiB is bounded, exceeds the probe worker's 4-MiB PHP limit, and stays
# below post_max_size. It forces request-parse OOM before application code.
stage=request_parse_oom
/opt/zbox/bin/php -r 'file_put_contents($argv[1], "field=" . str_repeat("x", 8*1024*1024));' "$root/post.txt"
set +e
code=$(curl --silent --show-error --max-time 10 -D "$root/oom-headers" -o "$root/oom-response" -w '%{http_code}' \
    -H 'Expect:' -H 'Content-Type: application/x-www-form-urlencoded' --data-binary "@$root/post.txt" \
    http://127.0.0.1:18089/probe.php)
curl_exit=$?
set -e
printf '%s\n' "PHP_OOM_RESPONSE curl_exit=$curl_exit http_status=$code"
printf '%s\n' OOM_RESPONSE_BASE64_BEGIN
if [ -f "$root/oom-response" ]; then base64 "$root/oom-response"; fi
printf '%s\n' OOM_RESPONSE_BASE64_END
printf '%s\n' OOM_HEADERS_BASE64_BEGIN
base64 "$root/oom-headers"
printf '%s\n' OOM_HEADERS_BASE64_END
case "$curl_exit:$code" in 0:500|52:000) ;; *) exit 1 ;; esac
if [ "$curl_exit" -eq 52 ]; then test ! -s "$root/oom-response" && test ! -s "$root/oom-headers"; fi
observed=false
for attempt in $(seq 1 200); do
    if [ -f /test-support/oom-verified ]; then observed=true; break; fi
    sleep 0.1
done
test "$observed" = true
kill -0 "$apache_pid"
curl --silent --show-error --fail --max-time 5 http://127.0.0.1:18089/probe.php > "$root/after-oom.json"
/opt/zbox/bin/php -r '$a=json_decode(file_get_contents($argv[1]),true);$b=json_decode(file_get_contents($argv[2]),true);if($a["worker_pid"]===$b["worker_pid"]||$b["post_fields"]!==0||$b["status"]!=="ok")exit(1);echo "PHP_POST_OOM_WORKER ",$b["worker_pid"],PHP_EOL;' "$root/response.json" "$root/after-oom.json"
printf '%s\n' PHP_REQUEST_PARSE_OOM_RECOVERY_PASSED
stage=session_fatal
code=$(curl --silent --show-error --max-time 5 -o "$root/fatal-response" -w '%{http_code}' \
    http://127.0.0.1:18089/session-fatal.php)
test "$code" = 500
grep -q 'controlled_session_fatal' "$root/php-error.log"
curl --silent --show-error --fail --max-time 5 http://127.0.0.1:18089/early/probe.php > "$root/after-fatal.json"
cmp "$root/after-oom.json" "$root/after-fatal.json"
grep -q 'auto_detect_line_endings.*deprecated' "$root/php-error.log"
printf '%s\n' PHP_SESSION_FATAL_RECOVERY_PASSED
printf '%s\n' PHP_APACHE_PROBE_PASSED
