"""Offline trust-boundary and provider-contract regression tests."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.request
from unittest.mock import patch
import review as r
from test_review import comparison, event, finding, B, H

class AuditTests(unittest.TestCase):
    def test_api_endpoint_allowlist(self):
        for url in ['http://api.github.com/repos/x', 'https://api.github.com.evil.test/', 'https://api.openai.com@evil.test/', 'https://evil.test/']:
            with self.subTest(url=url), patch.object(r.urllib.request, 'build_opener') as opener:
                with self.assertRaises(r.Stop): r.request_json(url, 'secret')
                opener.assert_not_called()

    def test_redirect_never_forwards_authorization(self):
        req=urllib.request.Request('https://api.github.com/repos/x',headers={'Authorization':'Bearer secret'})
        for status in [301,302,303,307,308]:
            with self.subTest(status=status), self.assertRaises(r.Stop):
                r.NoRedirect().redirect_request(req,None,status,'redirect',{},'https://evil.test/')

    def test_http_response_size_limit(self):
        response=io.BytesIO(b'x'*2000001)
        with patch.object(r.urllib.request,'build_opener') as opener:
            opener.return_value.open.return_value=response
            with self.assertRaises(r.Stop): r.request_json('https://api.github.com/repos/x','secret')
            self.assertEqual(opener.return_value.open.call_count,1)

    def model_env(self):
        return {'AI_REVIEW_ENABLED':'true','AI_MODEL':'gpt-5.4-mini-2026-03-17','INPUT_USD_PER_MILLION':'1','OUTPUT_USD_PER_MILLION':'4.5','MAX_USD_PER_RUN':'1','MODEL_MAX_INPUT_TOKENS':'400000','PRICING_VALID_UNTIL':'2099-01-01','OPENAI_API_KEY':'test-only'}

    def test_provider_request_contract(self):
        reply={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"findings":[]}'}]}]}
        with patch.dict(os.environ,self.model_env()),patch.object(r,'request_json',return_value=reply) as api,patch.object(r,'verify_budget_reservation'):
            self.assertEqual(r.model_review([]),{'findings':[]})
            args=api.call_args.args
            self.assertEqual(args[0],'https://api.openai.com/v1/responses')
            self.assertFalse(args[2]['store'])
            self.assertEqual(args[2]['max_output_tokens'],2000)
            self.assertNotIn('tools',args[2])
            self.assertTrue(args[2]['text']['format']['strict'])
            self.assertEqual(api.call_count,1)

    def test_provider_refusal_and_incomplete_fail_closed(self):
        replies=[{'status':'incomplete'}, {'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'no'}]}]}, {'status':'completed','output':[]}]
        for reply in replies:
            with self.subTest(reply=reply),patch.dict(os.environ,self.model_env()),patch.object(r,'request_json',return_value=reply) as api,patch.object(r,'verify_budget_reservation'):
                with self.assertRaises(r.Stop): r.model_review([])
                self.assertEqual(api.call_count,1)

    def test_invalid_prices_never_call_provider(self):
        for key in ['INPUT_USD_PER_MILLION','OUTPUT_USD_PER_MILLION','MAX_USD_PER_RUN']:
            for value in ['nan','inf','0','-1']:
                env=self.model_env();env[key]=value
                with self.subTest(key=key,value=value),patch.dict(os.environ,env),patch.object(r,'request_json') as api:
                    with self.assertRaises(r.Stop): r.model_review([])
                    api.assert_not_called()

    def test_nonfinite_confidence_rejected(self):
        _,anchors,_=r.collect(comparison(),B,H)
        for value in [float('nan'),float('inf'),True]:
            f=finding();f['confidence']=value
            with self.assertRaises(r.Stop): r.validate({'findings':[f]},anchors)

    def test_empty_review_does_not_scan_issues(self):
        with tempfile.NamedTemporaryFile(mode='w') as tmp:
            json.dump(event(),tmp);tmp.flush()
            env={'GITHUB_EVENT_PATH':tmp.name,'GITHUB_REPOSITORY':'comoc/test','GH_TOKEN':'test','AI_PUBLISH_ENABLED':'true','REVIEW_FINDINGS':base64.b64encode(b'{"findings":[]}').decode()}
            with patch.dict(os.environ,env),patch.object(r,'request_json',return_value=comparison()) as api:
                r.main('publish');self.assertEqual(api.call_count,1)

    def test_workflow_credential_boundary_and_bootstrap_syntax(self):
        # Optional audit dependency; production runtime remains stdlib-only.
        try:
            import yaml
        except ImportError:
            self.skipTest('Optional PyYAML unavailable; workflow parser check not run')
        raw=Path('.github/workflows/ai-push-review.yml.template').read_text()
        workflow=yaml.load(raw,Loader=yaml.BaseLoader)
        self.assertEqual(workflow['on']['push']['branches'],['master'])
        self.assertEqual(workflow['concurrency']['queue'],'max')
        jobs=workflow['jobs']
        self.assertEqual(jobs['review']['permissions'],{'contents':'read'})
        self.assertEqual(jobs['reserve']['permissions'],{'contents':'write'})
        self.assertNotIn('OPENAI_API_KEY',json.dumps(jobs['reserve']))
        self.assertEqual(jobs['publish']['permissions'],{'contents':'read','issues':'write'})
        self.assertNotIn('OPENAI_API_KEY',json.dumps(jobs['publish']))
        for job in jobs.values():
            self.assertEqual(job['runs-on'],'ubuntu-latest')
            bootstrap=job['steps'][0]['run'].split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
            compile(bootstrap,'bootstrap','exec')
            self.assertIn('NoRedirect()',bootstrap)
        self.assertIn('REPLACE_WITH_AUDITED_40_CHARACTER_COMMIT_SHA',raw)

if __name__=='__main__': unittest.main()
