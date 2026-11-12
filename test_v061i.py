"""Offline synthetic tests for read-only v0.6.1i logic."""
from datetime import datetime, timezone, timedelta
import pandas as pd
import sys, types, json, hashlib
try:
    import v057f_odds_integrity
except ImportError:
    stubodds=types.ModuleType('v057f_odds_integrity')
    stubodds.iso_timestamp=lambda v:datetime.fromisoformat(str(v).replace('Z','+00:00')).astimezone(timezone.utc) if v else None
    sys.modules['v057f_odds_integrity']=stubodds
if 'v061_prospective' not in sys.modules:
    stub=types.ModuleType('v061_prospective')
    stub.digest=lambda value:hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()
    sys.modules['v061_prospective']=stub
import v061i_analysis as x
import v061_prospective as prospective

now=datetime.now(timezone.utc)
def iso(dt):return dt.isoformat()
rankings=[{'horse':'Alpha','rank':1,'score':90}, {'horse':'Beta','rank':2,'score':80}, {'horse':'Gamma','rank':3,'score':70}]
payload={'model_version':'v0.2','rankings':rankings,'source_times':{'form_response_received_utc':iso(now-timedelta(hours=2)), 'ranking_generated_utc':iso(now-timedelta(hours=1,minutes=55))}}
archive={'batch_id':'abc','race_date':'2026-11-12','track':'Seymour','race_number':1,'created_at':iso(now-timedelta(hours=1,minutes=50)),'scheduled_start_utc':iso(now-timedelta(hours=1)),'payload':payload}
archive['payload_sha256']=prospective.digest(payload)
def results():return [{'race_date':'2026-11-12','track':'Seymour','race_number':1,'horse':h,'horse_key':h.lower(),'result_status':'final','scratched':False,'finishing_position':p} for h,p in [('Alpha',3),('Beta',1),('Gamma',2)]]
a=x.prospective_accuracy([archive],results())
assert a.iloc[0]['status']=='EVALUABLE_PRE_SCHEDULE_PROVISIONAL'
assert a.iloc[0]['top1_won']==False and a.iloc[0]['top2_hit']==True and a.iloc[0]['top3_hit']==True
assert x.prediction_summary(a).iloc[2]['hit_rate_pct']==100
b=dict(archive,payload_sha256='invalid')
assert x.prospective_accuracy([b],results()).iloc[0]['status']=='MISSING_OR_INVALID_ARCHIVE_HASH'
truncated=results()[:2]
assert x.prospective_accuracy([archive],truncated).iloc[0]['status']=='OFFICIAL_RESULT_FIELD_INCOMPLETE'
assert x.distance_meters('1200')==1200 and x.distance_meters('1400m')==1400
assert x.distance_band('1200')=='SPRINT_600_1200M'
assert x.distance_band('1800')=='MIDDLE_1601_2000M'
assert x.distance_band('1 mile')=='UNKNOWN'
features=[{'batch_id':'r1','horse':'a','model_rank':1,'model_score':None,'recent_form':None}, {'batch_id':'r1','horse':'b','model_rank':2,'model_score':7.4,'recent_form':'123'}]
missing,summary,ranks=x.unscored_diagnostic(features)
assert len(missing)==1 and int(ranks.loc[ranks['model_rank']==1,'unscored'].iloc[0])==1
coverage,reason=x.odds_coverage(pd.DataFrame([{'batch_id':'b','race_date':'2026-11-12','track':'Seymour','race_number':1,'rank':1,'horse':'Alpha','status':'ELIGIBLE'}, {'batch_id':'b','race_date':'2026-11-12','track':'Seymour','race_number':1,'rank':2,'horse':'Beta','status':'MISSING_VERIFIABLY_TIMED_WIN_PLACE_QUOTES'}]),pd.DataFrame())
assert coverage[coverage['strategy']=='Top 1'].iloc[0]['quotes_complete']==True
assert coverage[coverage['strategy']=='Top 2'].iloc[0]['quotes_complete']==False
print('v0.6.1i offline unit tests passed')
