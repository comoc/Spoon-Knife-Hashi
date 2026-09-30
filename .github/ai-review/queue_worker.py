"""Durable master-push queue. Trusted code only; never executes queued source."""
import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
import review as r

REPO = 'comoc/Spoon-Knife-Hashi'
ROOT = 'https://api.github.com/repos/' + REPO
LEDGER_URL = ROOT + '/contents/ledger.json'
BRANCH = 'ai-review-budget'
# Last successful original-code smoke review, before this correction began.
MIGRATION_BASE = '1e5da901d4d8ee94d90c8ca123fa8584d9219a27'
STATES = {'pending', 'claimed', 'complete', 'attention', 'ignored'}
MAX_EVENTS = 3000
class Deferred(r.Stop): pass

def now_utc(): return datetime.now(timezone.utc)
def stamp(): return now_utc().strftime('%Y-%m-%dT%H:%M:%SZ')
def identity():
    value = os.environ.get('GITHUB_RUN_ID','') + ':' + os.environ.get('GITHUB_RUN_ATTEMPT','')
    r.require(re.fullmatch(r'[0-9]+:[0-9]+', value), 'missing run identity')
    return value

def require_context():
    r.require(os.environ.get('GITHUB_REPOSITORY') == REPO, 'queue repository mismatch')
    r.require(os.environ.get('GITHUB_REF') == 'refs/heads/master', 'queue requires master')

def in_blackout(now):
    return (now.day == 1 and now.hour < 1) or (now + timedelta(hours=1)).month != now.month

def event_key(before, after):
    return hashlib.sha256((REPO+'\nrefs/heads/master\n'+before+'\n'+after).encode()).hexdigest()

def event_payload(record):
    return {'repository':{'full_name':REPO,'default_branch':'master'},
            'ref':record['ref'],'before':record['before'],'after':record['after']}

def parse(doc):
    r.require(doc.get('type') == 'file' and doc.get('encoding') == 'base64'
              and r.SHA.fullmatch(doc.get('sha','')), 'invalid ledger metadata')
    data=json.loads(base64.b64decode(doc['content']))
    r.require(isinstance(data,dict) and data.get('version') in [1,2], 'unknown ledger version')
    if data['version']==1:
        # Existing validator checks every reservation; migration never edits them.
        r.parse_budget_ledger(doc)
        data={'version':2,'months':data['months'],'events':{}}
        migrated=True
    else: migrated=False
    r.require(set(data)=={'version','months','events'},'invalid queue envelope')
    legacy={'version':1,'months':data['months']}
    r.parse_budget_ledger(dict(doc,content=base64.b64encode(json.dumps(legacy).encode()).decode()))
    events=data['events']
    r.require(isinstance(events,dict) and len(events)<=MAX_EVENTS,'queue too large')
    for key, event in events.items():
        r.require(isinstance(event,dict) and set(event).issubset({'ref','before','after','state','created_at','claim','reason'}), 'invalid event fields')
        r.require(set(event)>={'ref','before','after','state','created_at'}, 'missing event fields')
        r.event_identity(event_payload(event), REPO)
        r.require(key==event_key(event['before'],event['after']) and event['state'] in STATES, 'event identity/state mismatch')
        r.require(isinstance(event['created_at'],str) and re.fullmatch(r'20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z',event['created_at']), 'invalid timestamp')
        if 'reason' in event:
            r.require(isinstance(event['reason'],str) and len(event['reason'])<=120, 'invalid reason')
        if 'claim' in event:
            claim=event['claim']
            r.require(isinstance(claim,dict) and set(claim)=={'id','month'}, 'invalid claim')
            r.require(claim['id'] in data['months'].get(claim['month'],[]), 'claim missing reservation')
        r.require(event['state']!='claimed' or 'claim' in event, 'unreserved claim')
    return data,migrated

def read():
    doc=r.request_json(LEDGER_URL+'?ref='+BRANCH,os.environ['GH_TOKEN'])
    data,migrated=parse(doc)
    return doc,data,migrated

def write(doc,data,message):
    raw=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
    r.require(len(raw)<1000000,'ledger storage bound')
    result=r.request_json(LEDGER_URL,os.environ['GH_TOKEN'],{
        'branch':BRANCH,'sha':doc['sha'],'message':message,
        'content':base64.b64encode(raw).decode()},method='PUT')
    expected=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    r.require(result.get('content',{}).get('sha')==expected
              and r.SHA.fullmatch(result.get('commit',{}).get('sha','')),'unconfirmed ledger write')
    return dict(doc,sha=expected,content=base64.b64encode(raw).decode())

def enqueue(data,before,after):
    if before==after: return False
    record={'ref':'refs/heads/master','before':before,'after':after,
            'state':'pending','created_at':stamp()}
    r.event_identity(event_payload(record),REPO)
    key=event_key(before,after)
    if key in data['events']: return False
    r.require(len(data['events'])<MAX_EVENTS,'queue capacity exceeded; manual recovery required')
    data['events'][key]=record
    return True

def output(name,value):
    r.require(re.fullmatch('[a-z_]+',name) and isinstance(value,str) and '\n' not in value,'unsafe workflow output')
    with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write(name+'='+value+'\n')

def coordinate():
    require_context()
    doc,data,migrated=read()
    changed=migrated
    # Recovery baseline includes every master commit during setup, even if an
    # old disabled workflow missed its push. Persist it before selecting work.
    if migrated:
        changed=enqueue(data,MIGRATION_BASE,os.environ['GITHUB_SHA']) or changed
    name=os.environ.get('GITHUB_EVENT_NAME','')
    r.require(name in ['push','schedule','workflow_dispatch'], 'unsupported queue event')
    if name=='push' and not migrated:
        with open(os.environ['GITHUB_EVENT_PATH']) as f:event=json.load(f)
        before,after=r.event_identity(event,REPO)
        changed=enqueue(data,before,after) or changed
    if changed:doc=write(doc,data,'Preserve pending master review events')
    pending=[(k,e) for k,e in data['events'].items() if e['state']=='pending']
    print('Queue: pending='+str(len(pending))+', attention_or_claimed='+str(sum(e['state'] in ['attention','claimed'] for e in data['events'].values())))
    if not pending:return
    if os.environ.get('AI_REVIEW_ENABLED')!='true':
        print('Pending reviews retained: review gate disabled.');return
    now=now_utc()
    if in_blackout(now):
        print('Pending reviews retained until after UTC month-boundary window.');return
    expiry=os.environ.get('PRICING_VALID_UNTIL','')
    if not re.fullmatch(r'20[0-9]{2}-[0-9]{2}-[0-9]{2}',expiry) or now.strftime('%Y-%m-%d')>expiry:
        print('Pending reviews retained: pricing approval missing or expired.');return
    month=now.strftime('%Y-%m')
    if len(data['months'].get(month,[]))>=100:
        print('Pending reviews retained: USD100 monthly reservation cap reached.');return
    # Bound read-only triage work. Non-source deployment/README changes do not
    # burn paid slots; unsupported diffs remain visible as attention-required.
    for key,event in sorted(pending,key=lambda item:(item[1]['created_at'],item[0]))[:10]:
        try:
            compare=r.request_json(ROOT+'/compare/'+event['before']+'...'+event['after'],os.environ['GH_TOKEN'])
            files,_,_=r.collect(compare,event['before'],event['after'])
        except r.Stop:
            event['state']='attention';event['reason']='unsupported_or_incomplete_diff'
            doc=write(doc,data,'Flag unsupported queued review for attention');continue
        if not files:
            event['state']='ignored';event['reason']='no_eligible_application_source'
            doc=write(doc,data,'Record queued push with no eligible application source');continue
        claim_id=identity()
        r.require(all(claim_id not in entries for entries in data['months'].values()),'attempt already reserved; no automatic paid retry')
        # Recheck clock after potentially slow comparison reads.
        now=now_utc()
        if in_blackout(now) or now.strftime('%Y-%m')!=month:
            print('Pending reviews retained: clock crossed safe reservation window.');return
        data['months'].setdefault(month,[]).append(claim_id)
        event['state']='claimed';event['claim']={'id':claim_id,'month':month}
        event.pop('reason',None)
        write(doc,data,'Reserve USD1 and claim one queued master review')
        output('event_key',key)
        return

def selected():
    require_context()
    key=os.environ.get('QUEUE_EVENT_KEY','')
    r.require(re.fullmatch('[0-9a-f]{64}',key),'invalid selected event')
    doc,data,_=read()
    event=data['events'].get(key)
    r.require(event is not None and event['state']=='claimed' and event['claim']['id']==identity(),'queue claim mismatch')
    return doc,data,key,event

def run_review():
    outcome='not_started'
    attempted=False
    def before_paid():
        nonlocal attempted
        _,_,_,fresh=selected()
        now=now_utc()
        if in_blackout(now) or fresh['claim']['month']!=now.strftime('%Y-%m'):
            raise Deferred('defer queued review until valid month window')
        attempted=True # No automatic retry past this point, even on timeout.
    try:
        _,_,_,event=selected()
        r.main('review',budget_check=before_paid,event_override=event_payload(event))
        outcome='completed'
    except Deferred:
        outcome='deferred'
        print('Queued review deferred before any paid request.')
    except Exception:
        outcome='uncertain' if attempted else 'not_started'
        raise
    finally:
        output('review_outcome',outcome)

def publish():
    _,_,_,event=selected()
    r.main('publish',event_override=event_payload(event))

def finalize():
    doc,data,key,event=selected()
    outcome=os.environ.get('REVIEW_OUTCOME','')
    review_result=os.environ.get('REVIEW_RESULT','')
    publish_result=os.environ.get('PUBLISH_RESULT','')
    if outcome=='deferred' and review_result=='success':
        event['state']='pending';event['reason']='deferred_before_paid_request'
        event.pop('claim',None) # Reservation stays permanently in months.
    elif outcome=='completed' and review_result=='success' and (
            publish_result=='success' or (os.environ.get('AI_PUBLISH_ENABLED')!='true' and publish_result=='skipped')):
        event['state']='complete';event['reason']='review_completed'
    else:
        event['state']='attention'
        event['reason']='paid_or_publication_uncertain' if outcome in ['uncertain','completed',''] else 'configuration_or_preflight_failed'
    write(doc,data,'Record queued review outcome without refund or paid retry')
    print('Queue outcome: '+event['state']+' ('+event['reason']+').')

def main(mode):
    r.require(mode in ['coordinate','review','publish','finalize'],'invalid queue mode')
    {'coordinate':coordinate,'review':run_review,'publish':publish,'finalize':finalize}[mode]()

if __name__=='__main__':
    try:main(sys.argv[1])
    except Exception as exc:
        print('Queue stopped safely: '+(str(exc) if isinstance(exc,r.Stop) else type(exc).__name__)+'.',file=sys.stderr)
        sys.exit(1)
