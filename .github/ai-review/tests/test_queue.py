import base64, copy, hashlib, json, os, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import queue_worker as q
import review as r
from test_review import B,H,comparison

class FakeGit:
    def __init__(self,data):
        self.data=copy.deepcopy(data);self.sha='a'*40;self.writes=0
    def doc(self):
        return {'type':'file','encoding':'base64','sha':self.sha,'content':base64.b64encode(json.dumps(self.data).encode()).decode()}
    def __call__(self,url,token,data=None,method=None):
        if '/compare/' in url:
            before,after=url.split('/compare/')[1].split('...');c=comparison()
            c['base_commit']['sha']=c['merge_base_commit']['sha']=before;c['commits'][0]['sha']=after
            return c
        if data is None:return self.doc()
        assert method=='PUT' and data['sha']==self.sha
        raw=base64.b64decode(data['content']);self.data=json.loads(raw)
        self.sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest();self.writes+=1
        return {'content':{'sha':self.sha},'commit':{'sha':'c'*40}}

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        event={'repository':{'full_name':q.REPO,'default_branch':'master'},'ref':'refs/heads/master','before':B,'after':H}
        self.path=Path(self.tmp.name);(self.path/'event').write_text(json.dumps(event));(self.path/'output').write_text('')
        self.env={'GITHUB_REPOSITORY':q.REPO,'GITHUB_REF':'refs/heads/master','GITHUB_SHA':H,'GITHUB_EVENT_NAME':'push','GITHUB_EVENT_PATH':str(self.path/'event'),'GITHUB_OUTPUT':str(self.path/'output'),'GITHUB_RUN_ID':'555','GITHUB_RUN_ATTEMPT':'1','GH_TOKEN':'test','AI_REVIEW_ENABLED':'true','AI_PUBLISH_ENABLED':'true','PRICING_VALID_UNTIL':'2026-10-31'}
        self.enter(patch.dict(os.environ,self.env));self.enter(patch.object(q,'MIGRATION_BASE',B))
        self.clock=self.enter(patch.object(q,'now_utc',return_value=datetime(2026,9,15,12,tzinfo=timezone.utc)))
    def enter(self,cm):v=cm.start();self.addCleanup(cm.stop);return v
    def fake(self,data=None):
        self.git=FakeGit(data or {'version':1,'months':{'2026-09':['36723847578:1','36724300397:1']}})
        self.api=self.enter(patch.object(r,'request_json',side_effect=self.git));return self.git
    def chosen(self):
        q.coordinate();key=q.event_key(B,H);os.environ['QUEUE_EVENT_KEY']=key;return self.git.data['events'][key]
    def test_migration_reserves_and_preserves(self):
        self.fake();event=self.chosen()
        self.assertEqual(self.git.data['months']['2026-09'],['36723847578:1','36724300397:1','555:1'])
        self.assertEqual(len(self.git.data['events']),1);self.assertEqual(event['state'],'claimed')
    def test_blackout_persists_then_recovers_next_month(self):
        self.fake();self.clock.return_value=datetime(2026,9,30,23,30,tzinfo=timezone.utc)
        q.coordinate();key=q.event_key(B,H);self.assertEqual(self.git.data['events'][key]['state'],'pending')
        self.assertEqual(len(self.git.data['months']['2026-09']),2)
        self.clock.return_value=datetime(2026,10,1,1,17,tzinfo=timezone.utc)
        os.environ['GITHUB_EVENT_NAME']='schedule';q.coordinate()
        self.assertEqual(self.git.data['months']['2026-10'],['555:1'])
        self.assertEqual(self.git.data['events'][key]['state'],'claimed')
    def test_disabled_gate_still_enqueues(self):
        self.fake();os.environ['AI_REVIEW_ENABLED']='false';q.coordinate()
        self.assertEqual(self.git.data['events'][q.event_key(B,H)]['state'],'pending')
    def test_duplicate_delivery_no_new_event_or_reservation(self):
        self.fake();self.chosen();q.coordinate()
        self.assertEqual(len(self.git.data['events']),1);self.assertEqual(len(self.git.data['months']['2026-09']),3)
    def test_budget_full_preserves_pending(self):
        self.fake({'version':1,'months':{'2026-09':[str(i)+':1' for i in range(100)]}});q.coordinate()
        self.assertEqual(self.git.data['events'][q.event_key(B,H)]['state'],'pending')
        self.assertEqual(len(self.git.data['months']['2026-09']),100)
    def test_expired_pricing_preserves_pending(self):
        self.fake();os.environ['PRICING_VALID_UNTIL']='2026-09-01';q.coordinate()
        self.assertEqual(self.git.data['events'][q.event_key(B,H)]['state'],'pending')
    def test_month_delay_requeues_without_refund(self):
        self.fake();self.chosen();self.clock.return_value=datetime(2026,9,30,23,1,tzinfo=timezone.utc)
        def fake_main(mode,budget_check=None,event_override=None):budget_check()
        with patch.object(r,'main',side_effect=fake_main):q.run_review()
        self.assertIn('review_outcome=deferred', (self.path/'output').read_text())
        os.environ.update(REVIEW_OUTCOME='deferred',REVIEW_RESULT='success',PUBLISH_RESULT='skipped')
        q.finalize();self.assertEqual(self.git.data['events'][q.event_key(B,H)]['state'],'pending')
        self.assertEqual(len(self.git.data['months']['2026-09']),3)
    def test_paid_timeout_never_requeues(self):
        self.fake();self.chosen()
        def fake_main(mode,budget_check=None,event_override=None):budget_check();raise TimeoutError()
        with patch.object(r,'main',side_effect=fake_main):
            with self.assertRaises(TimeoutError):q.run_review()
        self.assertIn('review_outcome=uncertain',(self.path/'output').read_text())
        os.environ.update(REVIEW_OUTCOME='uncertain',REVIEW_RESULT='failure',PUBLISH_RESULT='skipped');q.finalize()
        self.assertEqual(self.git.data['events'][q.event_key(B,H)]['state'],'attention')
        q.coordinate();self.assertEqual(len(self.git.data['months']['2026-09']),3)
    def test_success_finalizes(self):
        self.fake();self.chosen();os.environ.update(REVIEW_OUTCOME='completed',REVIEW_RESULT='success',PUBLISH_RESULT='success')
        q.finalize();self.assertEqual(self.git.data['events'][q.event_key(B,H)]['state'],'complete')
    def test_partial_rerun_cannot_requeue_old_claim(self):
        self.fake();self.chosen();os.environ.update(GITHUB_RUN_ATTEMPT='2',REVIEW_OUTCOME='deferred',REVIEW_RESULT='success')
        with self.assertRaises(r.Stop):q.finalize()
        self.assertEqual(self.git.data['events'][q.event_key(B,H)]['state'],'claimed')
    def test_ambiguous_missing_outcome_needs_attention(self):
        self.fake();self.chosen();q.finalize()
        self.assertEqual(self.git.data['events'][q.event_key(B,H)]['state'],'attention')
    def test_corrupt_event_cannot_be_selected(self):
        self.fake();self.chosen();self.git.data['events'][q.event_key(B,H)]['after']='c'*40
        with self.assertRaises(r.Stop):q.selected()
    def test_compare_request_failure_leaves_pending(self):
        self.fake();self.api.side_effect=lambda url,*args,**kw: (_ for _ in ()).throw(TimeoutError()) if '/compare/' in url else self.git(url,*args,**kw)
        with self.assertRaises(TimeoutError):q.coordinate()
        self.assertEqual(self.git.data['events'][q.event_key(B,H)]['state'],'pending')
    def test_deployment_source_excluded(self):
        c=comparison();c['files'][0]['filename']='.github/ai-review/review.py'
        self.assertEqual(r.collect(c,B,H)[0],[])
    def test_workflow_permissions_and_schedule(self):
        import yaml
        raw=Path('.github/workflows/ai-push-review-queued.yml.template').read_text();w=yaml.load(raw,Loader=yaml.BaseLoader)
        self.assertEqual(w['on']['schedule'][0]['cron'],'17 * * * *')
        self.assertEqual(w['jobs']['review']['permissions'],{'contents':'read'})
        self.assertEqual(w['jobs']['publish']['permissions'],{'contents':'read','issues':'write'})
        for key in ['coordinate','finalize']:
            self.assertEqual(w['jobs'][key]['permissions'],{'contents':'write'})
            self.assertNotIn('OPENAI_API_KEY',json.dumps(w['jobs'][key]))
        self.assertNotIn('vars.AI_REVIEW_ENABLED',w['jobs']['coordinate']['if'])
        for job in w['jobs'].values():
            script=job['steps'][0]['run'].split("<<'PYBOOT'\n")[1].rsplit('\nPYBOOT',1)[0];compile(script,'bootstrap','exec')
if __name__=='__main__':unittest.main()
