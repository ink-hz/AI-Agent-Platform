#!/bin/sh
# Approved, exact CLI PHPT scope; stop on the first failure or missing coverage.
set -eu
cd /build/work/php-src-1d7dec178a094e6e75b229ce22754045f8c6070a
export NO_INTERACTION=1 REPORT_EXIT_STATUS=1
mkdir -p /build/native-empty-ini.d
: > /build/native-empty.ini
export PHPRC=/build/native-empty.ini PHP_INI_SCAN_DIR=/build/native-empty-ini.d
# Make's PHP_TEST_SHARED_EXTENSIONS owns the one newly built OPcache load.
export TEST_PHP_ARGS=-n
mode=${1:-applicable}
case "$mode" in
    applicable) scope=/build/php-native-tests.txt; expected=10 ;;
    compatibility-diagnostic) scope=/build/php-oom-compat-tests.txt; expected=2 ;;
    *) exit 2 ;;
esac
batch="/build/logs/native-$mode"
mkdir "$batch"
: > "$batch/combined.log"
index=0
while IFS= read -r test_name; do
    test -f "$test_name"
    index=$((index + 1))
    log="$batch/test-$index.log"
    if ! make test TESTS="$test_name" > "$log" 2>&1; then
        cat "$log" >> "$batch/combined.log"
        printf '%s\n' "PHP_NATIVE_TEST_FAILED $test_name" >&2
        exit 1
    fi
    cat "$log" >> "$batch/combined.log"
    if grep -Eq 'Tests (failed|skipped|warned|leaked)[[:space:]]*:[[:space:]]*[1-9]' "$log" \
        || ! grep -Eq 'Tests passed[[:space:]]*:[[:space:]]*1[[:space:]]' "$log"; then
        printf '%s\n' "PHP_NATIVE_TEST_COVERAGE_FAILED $test_name" >&2
        exit 1
    fi
    printf '%s\n' "PHP_NATIVE_TEST_PASSED $test_name"
done < "$scope"
test "$index" -eq "$expected"
if [ "$mode" = compatibility-diagnostic ]; then
    printf '%s\n' PHP_COMPATIBILITY_DIAGNOSTIC_COMPLETE
    exit 0
fi
python3 - <<'PY'
import hashlib,json,pathlib
root=pathlib.Path('/build/artifacts')
lock=json.loads(pathlib.Path('/build/php-source.json').read_text())
paths=[root/name for name in ('php','libphp.so','opcache.so')]
paths += sorted((root/'lib').iterdir())
lock['files']={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
(root/'manifest.json').write_text(json.dumps(lock,indent=2)+'\n')
(root/'artifact.sha256').write_text(''.join(digest+'  '+name+'\n' for name,digest in lock['files'].items()))
PY
printf '%s\n' PHP_NATIVE_TESTS_COMPLETE
