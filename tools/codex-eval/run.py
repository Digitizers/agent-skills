#!/usr/bin/env python3
"""Independent Codex shadow evals; never native activation or Claude evidence.

Python 3.9+. Explicit local policy files only, no credential copying, no tools,
no existing report reuse. Live probes are manual and bounded; CI uses fakes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


class ProbeError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_events(stdout: str) -> str:
    messages = []
    completed = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProbeError('non-JSON CLI event; result withheld') from exc
        if not isinstance(event, dict):
            raise ProbeError('invalid CLI event; result withheld')
        if event.get('type') in ('error', 'turn.failed'):
            raise ProbeError('CLI reported a failed turn; result withheld')
        item = event.get('item') or {}
        if not isinstance(item, dict):
            raise ProbeError('invalid CLI item; result withheld')
        if event.get('type') in ('item.started', 'item.updated', 'item.completed'):
            kind = item.get('type')
            if kind not in ('reasoning', 'agent_message'):
                raise ProbeError(f'tool or unsupported item type {kind!r} in no-tools probe; result withheld')
            if event['type'] == 'item.completed' and kind == 'agent_message':
                messages.append(item.get('text', ''))
        if event.get('type') == 'turn.completed':
            completed = True
    if not completed or not messages or not isinstance(messages[-1], str):
        raise ProbeError('CLI has no completed final answer; result withheld')
    return messages[-1]


def probe(prompt: str, model: str | None, timeout: int) -> str:
    with tempfile.TemporaryDirectory(prefix='codex-shadow-') as directory:
        command = ['codex', '--no-daemon', '-a', 'never', 'exec',
                   '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
                   '--sandbox', 'read-only', '--json', '-C', directory,
                   '-c', 'web_search="disabled"',
                   '-c', 'features.skip_host_skill_discovery=true',
                   '-c', 'suppress_unstable_features_warning=true']
        for feature in ('shell_tool', 'unified_exec', 'apps', 'browser_use',
                        'computer_use', 'hooks', 'multi_agent'):
            command += ['--disable', feature]
        if model:
            command += ['--model', model]
        command += ['-']
        try:
            result = subprocess.run(command, input=prompt, text=True,
                                    capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProbeError('Codex unavailable or timed out; result withheld') from exc
    if result.returncode:
        # Never copy raw CLI logs into reports: they can contain local data.
        raise ProbeError(f'Codex exited {result.returncode}; result withheld')
    return parse_events(result.stdout)


def measure(spec_path: Path, runs: int, model: str | None, timeout: int) -> dict:
    raw = spec_path.read_bytes()
    spec = json.loads(raw)
    if spec.get('schema_version') != 1 or not spec.get('cases'):
        raise ValueError('expected schema_version=1 and nonempty cases')
    if runs < 1 or runs > 20 or timeout < 1 or timeout > 300:
        raise ValueError('runs must be 1-20 and timeout 1-300 seconds')
    cases = spec['cases']
    if len({case['id'] for case in cases}) != len(cases):
        raise ValueError('duplicate case id')
    # Validate and freeze every input before spending quota.
    frozen = []
    for case in cases:
        if not case.get('prompt') or not isinstance(case.get('expected'), dict) or not case['expected']:
            raise ValueError('each case needs prompt and nonempty expected fields')
        documents = []
        for relative in case.get('files', []):
            path = (spec_path.parent / relative).resolve(strict=True)
            if path.suffix != '.md' or not path.is_file():
                raise ValueError('policy inputs must be explicit Markdown files')
            content = path.read_bytes()
            documents.append({'path': relative, 'sha256': digest(content),
                              'text': content.decode('utf-8')})
        prompt = ('This is a no-tools shadow evaluation, not a live user task. '
                  'Use only the supplied policy and scenario. Do not call tools, '
                  'read files, change configuration or contact anyone. Return one JSON '
                  'object with the decision fields requested by the scenario.\n\nPolicy:\n'
                  + '\n\n'.join(d['text'] for d in documents)
                  + '\n\nScenario:\n' + case['prompt'])
        frozen.append((case, documents, prompt))
    version = subprocess.run(['codex', '--version'], text=True, capture_output=True,
                             timeout=10, check=True).stdout.strip()
    results = []
    for case, documents, prompt in frozen:
        outputs = []
        for _ in range(runs):
            text = probe(prompt, model, timeout)
            try:
                answer = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProbeError('final answer is not JSON; result withheld') from exc
            if not isinstance(answer, dict):
                raise ProbeError('final answer is not an object; result withheld')
            passed = all(key in answer and type(answer[key]) is type(value) and answer[key] == value
                         for key, value in case['expected'].items())
            outputs.append({'pass': passed, 'answer': answer})
        results.append({'id': case['id'], 'runs': runs, 'outputs': outputs,
                        'prompt_sha256': digest(prompt.encode()),
                        'inputs': [{k: d[k] for k in ('path', 'sha256')} for d in documents]})
    # Refuse to certify inputs that moved during measurement.
    if spec_path.read_bytes() != raw:
        raise ProbeError('spec changed during evaluation; result withheld')
    for _, documents, _ in frozen:
        for d in documents:
            if digest((spec_path.parent / d['path']).read_bytes()) != d['sha256']:
                raise ProbeError('policy changed during evaluation; result withheld')
    return {'schema_version': 1, 'backend': 'codex-cli', 'mode': 'shadow-no-tools',
            'runtime_version': version, 'model_requested': model,
            'model_selection': 'explicit' if model else 'CLI default (user config ignored)',
            'measured_at': datetime.now(timezone.utc).isoformat(),
            'spec_sha256': digest(raw), 'instrument_sha256': digest(Path(__file__).read_bytes()),
            'results': results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=1)
    parser.add_argument('--model')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; reports are never overwritten or carried')
    try:
        report = measure(args.spec.resolve(), args.runs, args.model, args.timeout)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
    except (ProbeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f'{exc}\n')
    passed = sum(o['pass'] for r in report['results'] for o in r['outputs'])
    total = sum(r['runs'] for r in report['results'])
    print(f'Codex shadow probes: {passed}/{total} passed; not native activation evidence')
    return 0 if passed == total else 1


if __name__ == '__main__':
    raise SystemExit(main())
