from datetime import date
from unittest.mock import patch
import pytest

import v0423_bulk_results as v

RACE=('2026-11-21','Esperance',2)
PRED=[{'horse':'Horse A','model_rank':1,'predicted_at':'2026-11-21T00:00:00Z','prediction_batch_id':'b1'},
      {'horse':'Horse B','model_rank':2,'predicted_at':'2026-11-21T00:00:00Z','prediction_batch_id':'b1'}]


class Table:
    def __init__(self, db, name):self.db=db;self.name=name;self.filters=[];self.orders=[];self.limit_count=None
    def select(self,*args):return self
    def eq(self,k,x):self.filters.append((k,x));return self
    def order(self,k,**kwargs):self.orders.append(k);return self
    def limit(self,n):self.limit_count=n;return self
    def execute(self):
        if self.name=='runner_results': data=self.db.existing
        elif self.name=='abandoned_races':data=self.db.abandoned
        else:data=self.db.predictions
        data=[x for x in data if all(x.get(k)==w for k,w in self.filters)]
        if self.limit_count: data=data[:self.limit_count]
        self.db.reads.append((self.name,self.orders[:],self.filters[:]))
        return type('Resp',(),{'data':data})()
    def insert(self,rows):
        self.db.pending=rows
        return type('Q',(),{'execute':lambda qs: self.db.insert()})()

class DB:
    def __init__(self, predictions=None, existing=None, abandoned=None):
        self.predictions=predictions or [];self.existing=existing or [];self.abandoned=abandoned or []
        self.reads=[];self.pending=None;self.saved=[]
    def table(self,name):return Table(self,name)
    def insert(self):
        self.saved+=self.pending
        return type('Resp',(),{'data':self.pending})()


def preview(rows=None):
    return {'race':RACE,'batch':'b1','locked':['Horse A','Horse B'],
            'rows':rows or [],'status':'interim','stats':{'matched':2,'extra':0,'unresolved':0},'warnings':[]}


def filled(second=0.17):
    return [{'Horse':'Horse A','Status':'FINISHED','Position':1,'Margin (lengths)':second},
            {'Horse':'Horse B','Status':'FINISHED','Position':2,'Margin (lengths)':second}]

KW=dict(source='Sportsbet',reviewer='Admin',note='Verified final full field on Sportsbet',final_verified=True,complete_verified=True)


def test_unimported_filters_imported_and_abandoned():
    preds=[{'race_date':'2026-11-21','track':'Esperance','race_number':2},
           {'race_date':'2026-11-21','track':'Esperance','race_number':2},
           {'race_date':'2026-11-21','track':'Gosford','race_number':6},
           {'race_date':'2026-11-21','track':'Ascot','race_number':5}]
    done=[{'race_date':'2026-11-21','track':'Gosford','race_number':6}]
    abandoned=[{'race_date':'2026-11-21','track_key':'ascot','race_number':5}]
    assert v.select_unimported(preds,done,abandoned,asof=date(2026,11,22))==[RACE]


def test_earliest_batch_orders_tied_predictions_by_unique_id():
    db=DB(predictions=[dict(PRED[0],id='a',race_date=RACE[0],track=RACE[1],race_number=2),
                       dict(PRED[1],id='b',race_date=RACE[0],track=RACE[1],race_number=2)])
    _,chosen=v.select_original_batch(db,RACE)
    assert len(chosen)==2
    assert db.reads[-1][1]==['predicted_at','id']


def test_duplicate_in_source_rejected_not_deduplicated():
    db=DB(predictions=[dict(PRED[0],id='a',race_date=RACE[0],track=RACE[1],race_number=2),
                       dict(PRED[0],id='b',race_date=RACE[0],track=RACE[1],race_number=2)])
    with pytest.raises(ValueError,match='duplicate'):v.select_original_batch(db,RACE)


def test_duplicate_races_collapse():
    assert v.unique_races([RACE,RACE])==[RACE]


def test_nonfinal_provider_prefill_never_automatically_approved():
    db=DB(predictions=[dict(p,id=str(i),race_date=RACE[0],track=RACE[1],race_number=2) for i,p in enumerate(PRED)])
    payload={'date':RACE[0],'track':RACE[1],'raceNumber':2,'resultStatus':'interim','placings':[
        {'name':'Horse A','position':1,'margin':'0'}, {'name':'Horse B','position':2,'margin':'0.17'}]}
    with patch.object(v.manual,'fetch_formfav_preview',return_value=payload):
        result=v.prepare_preview(db,RACE,'fake')
    assert result['status']=='interim'
    assert result['rows'][0]['Margin (lengths)']==0.17
    with pytest.raises(ValueError,match='FINAL'):
        v.validate_for_save(result,filled(),**(KW|{'final_verified':False}))


def test_invalid_missing_nonfinisher_classification_blocked():
    rows=filled();rows[1]['Status']='UNSET';rows[1]['Position']=None
    with pytest.raises(ValueError,match='select FINISHED'):
        v.validate_for_save(preview(),rows,**KW)


def test_verified_lost_rider_saves_without_position():
    rows=[{'Horse':'Horse A','Status':'FINISHED','Position':1,'Margin (lengths)':None},
          {'Horse':'Horse B','Status':'LR','Position':None,'Margin (lengths)':None}]
    db=DB(predictions=[dict(p,id=str(i),race_date=RACE[0],track=RACE[1],race_number=2) for i,p in enumerate(PRED)])
    n=v.save_approved_race(db,preview(),rows,**KW)
    assert n==2
    assert db.saved[1]['finish_code']=='LR'
    assert db.saved[1]['finishing_position'] is None
    assert db.saved[1]['margin_lengths'] is None


def test_no_overwrite_existing_race():
    db=DB(existing=[{'race_date':RACE[0],'track':RACE[1],'race_number':2,'id':8}],predictions=[dict(p,id=str(i),race_date=RACE[0],track=RACE[1],race_number=2) for i,p in enumerate(PRED)])
    with pytest.raises(ValueError,match='Already imported'):
        v.save_approved_race(db,preview(),filled(),**KW)
    assert not db.saved


def test_abandoned_blocked():
    db=DB(abandoned=[{'race_date':RACE[0],'track_key':'esperance','race_number':2,'id':1}],predictions=[dict(p,id=str(i),race_date=RACE[0],track=RACE[1],race_number=2) for i,p in enumerate(PRED)])
    with pytest.raises(ValueError,match='Abandoned'):v.save_approved_race(db,preview(),filled(),**KW)


def test_missing_locked_horse_blocked():
    with pytest.raises(ValueError,match='locked runners missing'):
        v.validate_for_save(preview(),[filled()[0]],**KW)


def test_missing_reviewer_blocked():
    with pytest.raises(ValueError,match='Reviewer'):v.validate_for_save(preview(),filled(),**(KW|{'reviewer':''}))


def test_margin_mismatch_blocked():
    with pytest.raises(ValueError,match='disagree'):
        v.validate_for_save(preview(),filled(second=0.17)[:1]+[dict(filled()[1],**{'Margin (lengths)':0.18})],**KW)


def test_note_is_required():
    with pytest.raises(ValueError,match='verification note'):
        v.validate_for_save(preview(),filled(),**(KW|{'note':''}))


def test_source_is_recorded_and_url_optional():
    rows=v.build_insert_rows(v.validate_for_save(preview(),filled(),**KW),RACE,
        source='Sportsbet',reviewer='Admin',note='Verified result on Sportsbet',url='',now='2026-11-22T00:00:00+00:00')
    assert rows[0]['official_evidence_url'] is None
    assert rows[0]['source']=='ManualResults:Sportsbet'
    assert rows[0]['margin_type']=='WINNING'
    assert rows[1]['margin_type']=='BEATEN'


def test_stale_prediction_batch_blocks_writes():
    db=DB(predictions=[dict(p,id=str(i),race_date=RACE[0],track=RACE[1],race_number=2,prediction_batch_id='other') for i,p in enumerate(PRED)])
    with pytest.raises(ValueError,match='changed'):
        v.save_approved_race(db,preview(),filled(),**KW)


def test_provider_invalid_identity_doesnt_save():
    payload={'date':RACE[0],'track':'Gosford','raceNumber':2,'resultStatus':'final','placings':[]}
    class Response:
        def raise_for_status(self):pass
        def json(self):return payload
    with pytest.raises(ValueError,match='identity mismatch'):
        v.manual.fetch_formfav_preview('fake',RACE,http_get=lambda *a,**kw:Response())
