from app.engine import evaluate
from app.modules.allocate.rules import union_limits
from app.modules.reconcile.rules import reconcile
from app.config import ROOT
from decimal import Decimal
from copy import deepcopy

def rows_of(midday,device):
    return [r for r in midday['allocation_rows'] if r['device']==device]

def test_limit_union_real(midday):
    assert len(midday['limit_unions'])==1
    w=midday['limit_unions'][0];assert w['seconds']==2400;assert w['limit_kw']=='120';assert w['limit_ids']==['L1']

def test_station_limit_balanced_with_devices(midday):
    b=midday['balance']
    assert b['limit_curtail_kwh']=='3.3333';assert b['allocated_curtail_kwh']=='3.3333';assert b['limit_balanced']

def test_device_attribution_real(midday):
    a,b=midday['attribution']['devices']
    assert a=={'device':'INV-A','curtail_kwh':'2.5641','outage_kwh':'0.0000'}
    assert b=={'device':'INV-B','curtail_kwh':'0.7692','outage_kwh':'15.0000'}

def test_fault_slice_full_outage(midday):
    # 12:10 INV-B故障：全额基线记停运，可发为0，不参与限电分摊。
    row=next(r for r in rows_of(midday,'INV-B') if r['start'].endswith('04:10:00+00:00'))
    assert row['baseline_kw']=='50';assert row['outage_kw']=='50';assert row['available_kw']=='0';assert Decimal(row['curtail_kw'])==0

def test_string_outage_not_stacked_on_fault(midday):
    # OB1覆盖12:00-12:30，故障片不叠加组串停运，同一不可发电量只归一次。
    fault=next(r for r in rows_of(midday,'INV-B') if r['start'].endswith('04:10:00+00:00'))
    assert fault['outage_ids']==[];assert Decimal(fault['outage_kwh']).quantize(Decimal('.0001'))==Decimal('8.3333')

def test_no_double_count_outage_and_curtail(midday):
    # 同一设备同一片段：停运+限电不超过基线，互斥瓜分基线。
    for r in midday['allocation_rows']:
        if r['baseline_kw'] is None:continue
        assert Decimal(r['outage_kw'])+Decimal(r['curtail_kw'] or 0)<=Decimal(r['baseline_kw'])

def test_curtail_only_over_available(midday):
    # 场站限电只按可发（基线-停运）占比分摊，停运容量不进分摊基数。
    row=next(r for r in rows_of(midday,'INV-B') if r['start'].endswith('04:00:00+00:00'))
    assert row['available_kw']=='30';assert Decimal(row['curtail_kw'])==Decimal(10)*30/130

def test_pending_slice_not_zero_filled(midday):
    assert midday['balance']['pending_segments']==1;assert midday['balance']['pending_seconds']==600
    pending=midday['pending_slices'][0]
    assert pending['reasons']==['NO_REFERENCE'];assert pending['devices']==['INV-A']
    gap=next(r for r in rows_of(midday,'INV-A') if r['start']==pending['start'])
    assert gap['code']=='NO_REFERENCE';assert gap['curtail_kwh'] is None
    other=next(r for r in rows_of(midday,'INV-B') if r['start']==pending['start'])
    assert other['code']=='SLICE_PENDING';assert other['curtail_kwh'] is None

def test_pending_blocks_archive(midday):
    assert midday['archive']['status']=='待复核'

def test_no_limit_dataset_still_balanced(clean):
    assert clean['allocation_rows']==[];assert clean['balance']['limit_balanced'];assert clean['archive']['status']=='可归档'

def test_limit_conflict_both_isolated():
    text='id,kind,device,at,power,unit,irradiance,status,start,end\nL1,LIMIT,,,120,kW,,,2026-10-15T12:00:00+08:00,2026-10-15T12:30:00+08:00\nL2,LIMIT,,,130,kW,,,2026-10-15T12:10:00+08:00,2026-10-15T12:40:00+08:00\n'
    s=evaluate({'csv_text':text})
    assert sorted(r['id'] for r in s['quarantine'] if r['code']=='LIMIT_CONFLICT')==['L1','L2']
    assert s['limit_unions']==[];assert s['balance']['row_balanced']

def test_limit_with_device_rejected():
    text='id,kind,device,at,power,unit,irradiance,status,start,end\nL1,LIMIT,INV-A,,120,kW,,,2026-10-15T12:00:00+08:00,2026-10-15T12:30:00+08:00\n'
    s=evaluate({'csv_text':text})
    assert next(r for r in s['quarantine'] if r['id']=='L1')['code']=='LIMIT_DEVICE'

def test_outage_over_capacity_rejected():
    text='id,kind,device,at,power,unit,irradiance,status,start,end\nOB1,OUTAGE,INV-B,,60,kW,,,2026-10-15T12:00:00+08:00,2026-10-15T12:30:00+08:00\n'
    s=evaluate({'csv_text':text})
    assert next(r for r in s['quarantine'] if r['id']=='OB1')['code']=='INVALID_RANGE'

def test_event_overlap_slice_pending():
    # 设备级限电事件与场站限额混合的片段待查，不重复核算限电。
    text=(ROOT/'sample_data/midday.csv').read_text()+'EX1,EVENT,INV-A,,,,,,2026-10-15T12:00:00+08:00,2026-10-15T12:10:00+08:00\n'
    s=evaluate({'csv_text':text})
    hit=next(r for r in s['allocation_rows'] if r['device']=='INV-A' and r['start'].endswith('04:00:00+00:00'))
    assert hit['code']=='LIMIT_EVENT_OVERLAP';assert hit['curtail_kwh'] is None
    assert s['balance']['pending_segments']==2

def test_adjacent_same_value_limits_merge():
    limits=[{'id':'L1','start':'2026-10-15T04:00:00+00:00','end':'2026-10-15T04:30:00+00:00','limit_kw':'120'},{'id':'L2','start':'2026-10-15T04:30:00+00:00','end':'2026-10-15T04:40:00+00:00','limit_kw':'120'}]
    merged=union_limits(limits);assert len(merged)==1;assert merged[0]['limit_ids']==['L1','L2']
    other=[dict(l,limit_kw='130') if l['id']=='L2' else l for l in limits]
    assert len(union_limits(other))==2

def test_order_invariant_allocation(midday):
    lines=(ROOT/'sample_data/midday.csv').read_text().splitlines()
    reversed_text=lines[0]+'\n'+'\n'.join(reversed(lines[1:]))+'\n'
    again=evaluate({'csv_text':reversed_text})
    assert again['allocation_rows']==midday['allocation_rows'];assert again['attribution']==midday['attribution']

def test_allocation_tamper_breaks_balance(midday):
    s=deepcopy(midday);s['allocation_rows'][0]['curtail_kwh']='999';assert reconcile(s)['limit_balanced'] is False
