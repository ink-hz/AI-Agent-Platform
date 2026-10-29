<?php
/* Content-free CLI and Apache probe. Never include application configuration. */
$required = [
    'apcu', 'bcmath', 'bz2', 'calendar', 'Core', 'ctype', 'curl', 'date', 'dom',
    'exif', 'FFI', 'fileinfo', 'filter', 'ftp', 'gd', 'gettext', 'hash', 'iconv',
    'igbinary', 'imap', 'intl', 'ionCube Loader', 'json', 'ldap', 'libxml',
    'mbstring', 'mysqli', 'mysqlnd', 'openssl', 'pcntl', 'pcre', 'PDO',
    'pdo_mysql', 'pdo_pgsql', 'pgsql', 'Phar', 'posix', 'readline', 'redis',
    'Reflection', 'session', 'SimpleXML', 'SPL', 'standard', 'tokenizer',
    'xlswriter', 'xml', 'xmlreader', 'xmlwriter', 'Zend OPcache', 'zip', 'zlib',
];
$failure = null;
if (PHP_VERSION !== '8.1.34' || PHP_ZTS !== 0) $failure = 'runtime_version_or_thread_mode';
if (PHP_SAPI === 'apache2handler') {
    // Both baseline and candidate Apache expose 51 modules. These two are
    // CLI-only in upstream configure; Apache provides its own SAPI module.
    $required = array_values(array_diff($required, ['pcntl', 'readline']));
    $required[] = 'apache2handler';
} elseif (PHP_SAPI !== 'cli') {
    $failure = 'runtime_sapi_unreviewed';
}
foreach ($required as $name) {
    if (!extension_loaded($name)) $failure = 'required_extension_missing:' . $name;
}
if (!in_array(strtolower((string)ini_get('session.upload_progress.enabled')), ['', '0', 'off'], true)) {
    $failure = 'session_upload_progress_enabled';
}
ob_start();
phpinfo(INFO_GENERAL);
$general = strip_tags(ob_get_clean());
if (!preg_match('/PHP API\s*(?:=>)?\s*20210902\b/', $general)
    || !preg_match('/Zend Extension Build\s*(?:=>)?\s*API420210902,NTS\b/', $general)) {
    $failure = 'extension_abi_invalid';
}
if ($failure !== null) {
    http_response_code(500);
    error_log("PHP_RUNTIME_REJECTED " . $failure);
    exit(1);
}
header('Content-Type: application/json');
echo json_encode([
    'status' => 'ok', 'php_version' => PHP_VERSION, 'sapi' => PHP_SAPI,
    'php_api' => '20210902', 'zend_extension_build' => 'API420210902,NTS',
    'source_commit' => '1d7dec178a094e6e75b229ce22754045f8c6070a',
    'session_upload_progress' => false,
    'worker_pid' => getmypid(), 'post_fields' => count($_POST),
    'modules' => get_loaded_extensions(),
], JSON_THROW_ON_ERROR) . "\n";
