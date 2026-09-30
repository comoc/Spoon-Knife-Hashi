import base64, json, os, unittest
from datetime import datetime, timezone
from unittest.mock import patch
import review as r

NOW=datetime(2026,9,15,12,tzinfo=timezone.utc)
ENV={'GITHUB_REPOSITORY':'comoc/Spoon-Knife-Hashi','GITHUB_RUN_ID':'101','GITHUB_RUN_ATTEMPT':'1','GH_TOKEN':'test'}
def doc(months=None):
    return {'type':'file','encoding':'base64','sha':'a'*40,'content':base64.b64encode(json.dumps({'version':1,'months':months or {}}).encode()).decode()}
OK={'content':{'sha':'b'*40},'commit':{'sha':'c'*40}}
class BudgetTests(unittest.TestCase):
    def test_success_cas(self):
        with patch.dict(os.environ,ENV),patch.object(r,'request_json',side_effect=[doc(),OK]) as api:
            r.reserve_monthly_budget(NOW)
            data=api.call_args.args[2]
            self.assertEqual(data['sha'],'a'*40)
            self.assertEqual(api.call_args.kwargs,{'method':'PUT'})
            self.assertEqual(json.loads(base64.b64decode(data['content']))['months']['2026-09'],['101:1'])
    def test_exhausted_and_duplicate(self):
        for entries in [[str(i)+':1' for i in range(100)],['101:1']]:
            with patch.dict(os.environ,ENV),patch.object(r,'request_json',return_value=doc({'2026-09':entries})) as api:
                with self.assertRaises(r.Stop):r.reserve_monthly_budget(NOW)
                self.assertEqual(api.call_count,1)
    def test_last_reservation_allowed(self):
        with patch.dict(os.environ,ENV),patch.object(r,'request_json',side_effect=[doc({'2026-09':[str(i)+':1' for i in range(99)]}),OK]):
            r.reserve_monthly_budget(NOW)
    def test_missing_conflict_or_ambiguous_never_retry(self):
        for replies in [[TimeoutError()], [doc(),TimeoutError()], [doc(),{}]]:
            with patch.dict(os.environ,ENV),patch.object(r,'request_json',side_effect=replies) as api:
                with self.assertRaises(Exception):r.reserve_monthly_budget(NOW)
                self.assertEqual(api.call_count,len(replies))
    def test_corrupt_ledger(self):
        for months in [{'2026-09':['1:1','1:1']},{'garbage':[]},{'2026-09':[-1]}]:
            with patch.dict(os.environ,ENV),patch.object(r,'request_json',return_value=doc(months)) as api:
                with self.assertRaises(r.Stop):r.reserve_monthly_budget(NOW)
                self.assertEqual(api.call_count,1)
    def test_month_boundary_stops(self):
        for now in [datetime(2026,9,30,23,1,tzinfo=timezone.utc),datetime(2026,10,1,0,30,tzinfo=timezone.utc)]:
            with patch.dict(os.environ,ENV),patch.object(r,'request_json') as api:
                with self.assertRaises(r.Stop):r.reserve_monthly_budget(now)
                api.assert_not_called()
    def test_missing_reservation(self):
        with patch.dict(os.environ,ENV),patch.object(r,'request_json',return_value=doc()):
            with self.assertRaises(r.Stop):r.verify_budget_reservation()
    def model_env(self):
        return dict(ENV,AI_REVIEW_ENABLED='true',AI_MODEL='gpt-5.4-mini-2026-03-17',
                    INPUT_USD_PER_MILLION='0.75',OUTPUT_USD_PER_MILLION='4.5',
                    MAX_USD_PER_RUN='1',MODEL_MAX_INPUT_TOKENS='400000',
                    PRICING_VALID_UNTIL='2026-10-31',OPENAI_API_KEY='test-only')
    def test_delayed_review_at_month_boundary_never_calls_provider(self):
        # Reservation can precede review-environment approval by hours or days.
        # An otherwise valid ledger must not allow a delayed paid call near UTC midnight.
        for now in [datetime(2026,9,30,23,0,tzinfo=timezone.utc),
                    datetime(2026,9,30,23,59,59,tzinfo=timezone.utc),
                    datetime(2026,10,1,0,0,tzinfo=timezone.utc),
                    datetime(2026,10,1,0,59,59,tzinfo=timezone.utc)]:
            with self.subTest(now=now),patch.dict(os.environ,self.model_env()),\
                    patch.object(r,'datetime') as clock,\
                    patch.object(r,'request_json',return_value=doc({now.strftime('%Y-%m'):['101:1']})) as api:
                clock.now.return_value=now
                with self.assertRaisesRegex(r.Stop,'month boundary safety window'):
                    r.model_review([])
                self.assertEqual(api.call_count,1)
                self.assertTrue(api.call_args.args[0].startswith('https://api.github.com/'))
    def test_old_month_reservation_never_carries_forward(self):
        with patch.dict(os.environ,self.model_env()),patch.object(r,'datetime') as clock,\
                patch.object(r,'request_json',return_value=doc({'2026-09':['101:1']})) as api:
            clock.now.return_value=datetime(2026,10,1,1,tzinfo=timezone.utc)
            with self.assertRaisesRegex(r.Stop,'no reservation for this run attempt'):
                r.model_review([])
            self.assertEqual(api.call_count,1)
    def test_partial_rerun_without_new_reservation_never_calls_provider(self):
        env=self.model_env();env['GITHUB_RUN_ATTEMPT']='2'
        with patch.dict(os.environ,env),patch.object(r,'datetime') as clock,\
                patch.object(r,'request_json',return_value=doc({'2026-09':['101:1']})) as api:
            clock.now.return_value=NOW
            with self.assertRaisesRegex(r.Stop,'no reservation for this run attempt'):
                r.model_review([])
            self.assertEqual(api.call_count,1)
    def test_full_rerun_burns_another_slot(self):
        env=dict(ENV,GITHUB_RUN_ATTEMPT='2')
        with patch.dict(os.environ,env),patch.object(r,'request_json',side_effect=[doc({'2026-09':['101:1']}),OK]) as api:
            r.reserve_monthly_budget(NOW)
            ledger=json.loads(base64.b64decode(api.call_args.args[2]['content']))
            self.assertEqual(ledger['months']['2026-09'],['101:1','101:2'])
    def test_paid_call_allowed_outside_boundary_with_exact_reservation(self):
        reply={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"findings":[]}'}]}]}
        for now in [datetime(2026,9,30,22,59,59,tzinfo=timezone.utc),
                    datetime(2026,10,1,1,tzinfo=timezone.utc)]:
            with self.subTest(now=now),patch.dict(os.environ,self.model_env()),\
                    patch.object(r,'datetime') as clock,\
                    patch.object(r,'request_json',side_effect=[doc({now.strftime('%Y-%m'):['101:1']}),reply]) as api:
                clock.now.return_value=now
                self.assertEqual(r.model_review([]),{'findings':[]})
                self.assertEqual(api.call_count,2)
                self.assertEqual(api.call_args.args[0],'https://api.openai.com/v1/responses')
    def test_reviewer_rejects_corrupt_month(self):
        for entries in ['prefix101:1suffix',['101:1']*101]:
            with patch.dict(os.environ,ENV),patch.object(r,'request_json',return_value=doc({'2026-09':entries})):
                with self.assertRaises(r.Stop):r.verify_budget_reservation()
    def test_html_eligible(self):
        from test_review import comparison,B,H
        c=comparison();c['files'][0]['filename']='index.html'
        self.assertEqual(len(r.collect(c,B,H)[0]),1)
if __name__=='__main__':unittest.main()
