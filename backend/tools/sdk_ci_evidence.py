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



def invalidate_explicit_output(argv):
    """Clear only one explicit output, preserving every identifiable input."""
    values = {'--input': [], '--output': []}
    for index, token in enumerate(argv):
        if token == '--':
            break
        option, separator, value = token.partition('=')
        if option not in values:
            continue
        if not separator:
            value = argv[index + 1] if index + 1 < len(argv) else ''
            if value.startswith('-'):
                value = ''
        values[option].append(value)
    outputs = values['--output']
    if len(outputs) > 1:
        raise ValueError('ambiguous output')
    if not outputs or not outputs[0]:
        return
    output = Path(outputs[0])
    for value in values['--input']:
        if not value:
            continue
        source = Path(value)
        if os.path.abspath(source) == os.path.abspath(output):
            raise ValueError('same input and output')
        try:
            resolved_source = source.resolve()
        except RuntimeError:
            resolved_source = None
        if resolved_source == output.resolve() or (
                source.exists() and output.exists() and os.path.samefile(source, output)):
            raise ValueError('same input and output')
    output.unlink(missing_ok=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        invalidate_explicit_output(argv)
    except (OSError, ValueError, RuntimeError):
        print('CI evidence input or output error', file=sys.stderr)
        return 3
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args, unknown = parser.parse_known_args(argv)
    temporary = None
    try:
        source, output = args.input, args.output
        if os.path.abspath(source) == os.path.abspath(output):
            raise ValueError('same input and output')
        try:
            resolved_source = source.resolve()
        except RuntimeError:
            # A looping input cannot identify a regular input file. Still invalidate
            # a distinct explicit output before reporting the input error.
            resolved_source = None
        if resolved_source == output.resolve() or (
                source.exists() and output.exists() and os.path.samefile(source, output)):
            raise ValueError('same input and output')
        # Invalidate the prior receipt before reading or validating this run.
        output.unlink(missing_ok=True)
        if unknown or resolved_source is None:
            raise ValueError('invalid arguments or input path')
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
    except (OSError, ValueError, TypeError, UnicodeError, RuntimeError):
        print('CI evidence input or output error', file=sys.stderr)
        return 3
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
