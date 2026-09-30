import json, os, tempfile, unittest
from unittest.mock import patch
import review as r
B='a'*40; H='b'*40

def comparison():
    return {'status':'ahead','base_commit':{'sha':B},'merge_base_commit':{'sha':B},'total_commits':1,'commits':[{'sha':H}], 'files':[{'filename':'src/app.py','status':'modified','additions':1,'deletions':1,'patch':'@@ -1,2 +1,2 @@\n-old\n+new\n context'}]}
def finding():
    return {'bug_key':'inverted-empty-input-condition','title':'結果が誤って返される','path':'src/app.py','line':1,'severity':'P2','confidence':.97,'category':'correctness','cause':'戻り値の条件が逆になっている','impact':'利用者に誤った結果を返す','reproduction':'空の入力を指定すると発生する','fix':'条件の真偽を正しい向きにする'}
def event():
    return {'repository':{'full_name':'comoc/test','default_branch':'main'},'ref':'refs/heads/main','before':B,'after':H}

class Tests(unittest.TestCase):
    def test_valid(self):
        files,anchors,skipped=r.collect(comparison(),B,H)
        self.assertEqual(r.validate({'findings':[finding()]},anchors),[finding()])
    def test_empty_findings(self): self.assertEqual(r.validate({'findings':[]},{}),[])
    def test_push_variants(self):
        for key in ['forced','created','deleted']:
            with self.subTest(key=key),self.assertRaises(r.Stop): r.event_identity(dict(event(),**{key:True}),'comoc/test')
    def test_bad_baselines(self):
        for before in ['0'*40,'bad']:
            with self.assertRaises(r.Stop): r.event_identity(dict(event(),before=before),'comoc/test')
    def test_wrong_repo_branch(self):
        with self.assertRaises(r.Stop): r.event_identity(event(),'other/repo')
        with self.assertRaises(r.Stop): r.event_identity(dict(event(),ref='refs/heads/feature'),'comoc/test')
    def test_incomplete_comparisons(self):
        for key,value in [('status','diverged'),('total_commits',2),('merge_base_commit',{'sha':H}),('files',comparison()['files']*31)]:
            with self.subTest(key=key),self.assertRaises(r.Stop): r.collect(dict(comparison(),**{key:value}),B,H)
    def test_missing_truncated_secret_rename(self):
        for key,value in [('patch',''),('additions',3),('status','renamed'),('patch','@@ -1 +1 @@\n-old\n+api_key = "sensitive-value"')]:
            c=comparison();c['files'][0][key]=value
            with self.subTest(key=key),self.assertRaises(r.Stop):r.collect(c,B,H)
    def test_path_allowlist(self):
        for name in ['.env','secrets/key.py','../../x.py','foo.ts`','dist/app.js']:
            c=comparison();c['files'][0]['filename']=name
            self.assertEqual(r.collect(c,B,H)[0],[])
    def test_oversize_diff(self):
        c=comparison();c['files'][0]['patch']='@@ -1 +1 @@\n-old\n+'+'a'*25000
        with self.assertRaises(r.Stop):r.collect(c,B,H)
    def test_output_filters(self):
        _,a,_=r.collect(comparison(),B,H)
        for key,value in [('confidence',.5),('category','security'),('line',2),('line',True),('path','x.py'),('title','English title'),('cause','外部送信 https://bad.test'),('impact','通知 @everyone'),('fix','実行 `curl`'),('title','秘密 sk-1234567890123')]:
            f=finding();f[key]=value
            with self.subTest(key=key),self.assertRaises(r.Stop): r.validate({'findings':[f]},a)
    def test_schema_extra(self):
        f=finding();f['command']='echo secret'
        with self.assertRaises(r.Stop):r.validate({'findings':[f]}, {})
    def test_stable_fingerprint_line_shift(self):
        f=finding();fp=r.fingerprint('comoc/test',f,{'src/app.py':{1:'new'}})
        f['line']=5
        self.assertEqual(fp,r.fingerprint('comoc/test',f,{'src/app.py':{5:'new'}}))
    def test_distinct_bugs_same_line(self):
        f=finding();a={'src/app.py':{1:'new'}};fp=r.fingerprint('comoc/test',f,a)
        f['bug_key']='null-result-dereference'
        self.assertNotEqual(fp,r.fingerprint('comoc/test',f,a))
    def test_bad_bug_key(self):
        _,a,_=r.collect(comparison(),B,H);f=finding();f['bug_key']='https://bad.test'
        with self.assertRaises(r.Stop):r.validate({'findings':[f]},a)
    def test_disabled_no_paid_call(self):
        with patch.dict(os.environ,{'AI_REVIEW_ENABLED':'false'}), patch.object(r,'request_json') as api:
            with self.assertRaises(r.Stop):r.model_review([])
            api.assert_not_called()
    def test_cap_no_paid_call(self):
        env={'AI_REVIEW_ENABLED':'true','AI_MODEL':'test-model','INPUT_USD_PER_MILLION':'100','OUTPUT_USD_PER_MILLION':'100','MAX_USD_PER_RUN':'.01'}
        with patch.dict(os.environ,env),patch.object(r,'request_json') as api:
            with self.assertRaises(r.Stop):r.model_review([])
            api.assert_not_called()
    def test_publish_dedup(self):
        import base64
        f=finding();c=comparison();_,a,_=r.collect(c,B,H)
        marker='ai-push-review:v1:'+r.fingerprint('comoc/test',f,a)
        with tempfile.NamedTemporaryFile(mode='w') as tmp:
            json.dump(event(),tmp);tmp.flush()
            env={'GITHUB_EVENT_PATH':tmp.name,'GITHUB_REPOSITORY':'comoc/test','GH_TOKEN':'test','AI_PUBLISH_ENABLED':'true','REVIEW_FINDINGS':base64.b64encode(json.dumps({'findings':[f,f]}).encode()).decode()}
            with patch.dict(os.environ,env),patch.object(r,'request_json',side_effect=[c,[{'body':marker}]]) as api:
                r.main('publish');self.assertEqual(api.call_count,2)
            with patch.dict(os.environ,env),patch.object(r,'request_json',side_effect=[c,[],{'number':1}]) as api:
                r.main('publish');self.assertEqual(api.call_count,3)

if __name__=='__main__': unittest.main()
