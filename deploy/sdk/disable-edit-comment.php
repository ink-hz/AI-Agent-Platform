<?php
/* ZenTao OSS 22.6: disable the vulnerable action::editComment route.
 * CVE-2026-111346 allows an authenticated user to read and overwrite
 * another user's comments. The upstream image hash is checked so an
 * upgrade cannot silently reuse this patch against different source.
 */

$path = '/apps/zentao/module/action/control.php';
$expected = '35cb625405702546a499c61cc602d1328806b7bf1b16b5f44adea883866f4076';
if (hash_file('sha256', $path) !== $expected) {
    fwrite(STDERR, "Unexpected ZenTao action controller version.\n");
    exit(1);
}

$source = file_get_contents($path);
$needle = "    public function editComment(\$actionID)\n    {\n";
if ($source === false || substr_count($source, $needle) !== 1) {
    fwrite(STDERR, "Could not locate the exact editComment method.\n");
    exit(1);
}

$replacement = $needle . "        http_response_code(403);\n        exit;\n";
if (file_put_contents($path, str_replace($needle, $replacement, $source)) === false) {
    fwrite(STDERR, "Could not patch the action controller.\n");
    exit(1);
}
