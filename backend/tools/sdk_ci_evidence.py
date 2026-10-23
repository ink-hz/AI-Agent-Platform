"""Emit fail-closed runner-local CI evidence; never authorize promotion."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Direct invocation from the repository root must also find backend/app.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.sdk_workbench.ci_evidence import verify_ci_run


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(3, 'invalid command arguments\n')


def load_run(source):
    data = json.loads(source.read_text(encoding='utf-8'))
    expected = {'workspace_root', 'candidate_digest', 'run_id', 'attempt', 'exit_code',
                'artifacts', 'suites'}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError('invalid fields')
    root = data.pop('workspace_root')
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise ValueError('invalid workspace')
    if type(data['exit_code']) is not int or data['exit_code'] < 0:
        raise ValueError('invalid exit code')
    suites = data['suites']
    if not isinstance(suites, dict):
        raise TypeError('invalid suites')
    for name, config in suites.items():
        if not isinstance(config, dict) or set(config) != {'path', 'minimum_executed_cases'}:
            raise ValueError('invalid suite')
        suites[name] = (config['path'], config['minimum_executed_cases'])
    return verify_ci_run(Path(root), **data)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    temporary = None
    try:
        source, output = args.input, args.output
        if source.resolve() == output.resolve() or (
                source.exists() and output.exists() and os.path.samefile(source, output)):
            raise ValueError('same input and output')
        # Invalidate the prior receipt before reading or validating this run.
        output.unlink(missing_ok=True)
        if not output.parent.is_dir():
            raise ValueError('missing output directory')
        receipt = load_run(source)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         prefix='.sdk-ci-evidence-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(receipt, stream, ensure_ascii=False, sort_keys=True)
            stream.write('\n')
        os.replace(temporary, output)
        return 2 if receipt['state'] == 'blocked' else 0
    except (OSError, ValueError, TypeError, UnicodeError):
        print('CI evidence input or output error', file=sys.stderr)
        return 3
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
