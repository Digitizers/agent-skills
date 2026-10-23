import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('codex_shadow', Path(__file__).with_name('run.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def events(text='{}'):
    return '\n'.join(json.dumps(e) for e in [
        {'type':'item.completed','item':{'type':'agent_message','text':text}},
        {'type':'turn.completed','usage':{}}])


class EvalTests(unittest.TestCase):
    def test_completed_answer(self):
        self.assertEqual(M.parse_events(events('{"action":"draft"}')), '{"action":"draft"}')

    def test_errors_incomplete_and_tools_never_become_results(self):
        bad = ['not json', '{}', '{"type":"turn.failed"}',
               '{"type":"error"}',
               '{"type":"item.started","item":{"type":"command_execution"}}',
               '{"type":"item.completed","item":{"type":"mcp_tool_call"}}',
               '{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}']
        for data in bad:
            with self.subTest(data=data), self.assertRaises(M.ProbeError):
                M.parse_events(data)

    def test_probe_uses_ephemeral_readonly_isolation_and_stdin(self):
        with patch.object(M.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, events())) as run:
            M.probe('scenario', None, 60)
        command = run.call_args.args[0]
        for flag in ['--no-daemon','--ignore-user-config','--ephemeral','read-only',
                     'never','features.skip_host_skill_discovery=true']:
            self.assertIn(flag, command)
        self.assertEqual(run.call_args.kwargs['input'], 'scenario')
        self.assertNotIn('scenario', command)

    def test_nonzero_and_timeout_are_errors(self):
        for outcome in [subprocess.CompletedProcess([], 1, ''), subprocess.TimeoutExpired('codex',1)]:
            with self.subTest(outcome=outcome):
                with patch.object(M.subprocess,'run',side_effect=outcome if isinstance(outcome,Exception) else None,
                                  return_value=outcome), self.assertRaises(M.ProbeError):
                    M.probe('prompt',None,1)

    def test_reports_are_independent_and_identify_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); policy=root/'SKILL.md'; policy.write_text('policy')
            spec=root/'spec.json'; spec.write_text(json.dumps({'schema_version':1,'cases':[
                {'id':'one','prompt':'choose','files':['SKILL.md'],'expected':{'action':'draft'}}]}))
            with patch.object(M,'probe',return_value='{"action":"draft"}'), patch.object(M.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'codex-cli test')):
                report=M.measure(spec,2,None,1)
            self.assertEqual(report['backend'],'codex-cli')
            self.assertEqual(report['mode'],'shadow-no-tools')
            self.assertEqual(report['results'][0]['runs'],2)
            self.assertTrue(all(x['pass'] for x in report['results'][0]['outputs']))
            self.assertEqual(report['results'][0]['inputs'][0]['sha256'],M.digest(b'policy'))

    def test_changed_input_refuses_certification(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); p=root/'SKILL.md'; p.write_text('before')
            spec=root/'spec.json'; spec.write_text(json.dumps({'schema_version':1,'cases':[
                {'id':'one','prompt':'choose','files':['SKILL.md'],'expected':{'a':1}}]}))
            def changed(*args):
                p.write_text('after'); return '{"a":1}'
            with patch.object(M,'probe',side_effect=changed), patch.object(M.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'codex-cli test')):
                with self.assertRaises(M.ProbeError): M.measure(spec,1,None,1)

    def test_number_cannot_impersonate_boolean_expectation(self):
        with tempfile.TemporaryDirectory() as directory:
            spec=Path(directory)/'spec.json'
            spec.write_text(json.dumps({'schema_version':1,'cases':[
                {'id':'one','prompt':'choose','expected':{'activate':False}}]}))
            with patch.object(M,'probe',return_value='{"activate":0}'), patch.object(M.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'codex-cli test')):
                report=M.measure(spec,1,None,1)
            self.assertFalse(report['results'][0]['outputs'][0]['pass'])

    def test_bad_spec_is_rejected_before_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            spec=Path(directory)/'spec.json'; spec.write_text('{"schema_version":1,"cases":[]}')
            with patch.object(M,'probe') as probe, self.assertRaises(ValueError):
                M.measure(spec,1,None,1)
            probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
