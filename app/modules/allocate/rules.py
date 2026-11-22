from decimal import Decimal
from datetime import timedelta
from collections import defaultdict
from app.coerce import number,instant
from app.modules.baseline.rules import reference_baseline

PENDING_CODES={'TARGET_GAP','NO_REFERENCE','LIMIT_EVENT_OVERLAP'}

def union_limits(limits):
    # 同值限额重叠或相邻先求并集；异值重叠已在设备映射阶段双双隔离。
    merged=[]
    for row in sorted(limits,key=lambda r:(instant(r['start']),instant(r['end']),r['id'])):
        start,end=instant(row['start']),instant(row['end'])
        if merged and start<=merged[-1]['end'] and row['limit_kw']==merged[-1]['limit_kw']:
            merged[-1]['end']=max(end,merged[-1]['end']);merged[-1]['limit_ids'].append(row['id'])
        else:merged.append({'start':start,'end':end,'limit_kw':row['limit_kw'],'limit_ids':[row['id']]})
    return merged

def window_boundaries(window,samples,outages,cadence):
    boundaries={window['start'],window['end']}
    for rows in samples.values():
        for row in rows:
            at=instant(row['at']);expires=at+timedelta(seconds=cadence)
            if window['start']<at<window['end']:boundaries.add(at)
            if window['start']<expires<window['end']:boundaries.add(expires)
    for rows in outages.values():
        for row in rows:
            for edge in (instant(row['start']),instant(row['end'])):
                if window['start']<edge<window['end']:boundaries.add(edge)
    return sorted(boundaries)

def device_slice(device,zone,a,b,window,readings,outages,records,context,cadence):
    row={'device':device,'zone':zone,'start':a.isoformat(),'end':b.isoformat(),'seconds':int((b-a).total_seconds()),
         'limit_kw':window['limit_kw'],'limit_ids':list(window['limit_ids']),
         'sample_id':'','sample_at':'','actual_kw':None,'irradiance':None,'status':'缺测',
         'baseline_kw':None,'outage_kw':None,'available_kw':None,'curtail_kw':None,
         'outage_kwh':None,'curtail_kwh':None,'outage_ids':[],'reference_ids':[],'code':'TARGET_GAP'}
    covering_readings=[r for r in readings if instant(r['at'])<=a<instant(r['at'])+timedelta(seconds=cadence)]
    if not covering_readings:return row
    reading=covering_readings[-1]
    row.update(sample_id=reading['id'],sample_at=reading['at'],actual_kw=reading['power_kw'],irradiance=reading['irradiance'],status=reading['status'])
    estimate=reference_baseline(row,records,context)
    row['reference_ids']=estimate['reference_ids']
    if estimate['code']!='ESTIMATED':row['code']='NO_REFERENCE';return row
    baseline=number(estimate['baseline_kw']);row['baseline_kw']=estimate['baseline_kw']
    covering=[o for o in outages if instant(o['start'])<=a<instant(o['end'])]
    if reading['status']=='故障':
        # 设备故障全额停运，组串停运不重复叠加，同一不可发电量只归一次停运。
        outage=baseline
    else:
        outage=min(max((number(o['outage_kw']) for o in covering),default=Decimal(0)),baseline)
        row['outage_ids']=sorted(o['id'] for o in covering)
    hours=Decimal(row['seconds'])/3600
    row.update(outage_kw=str(outage),available_kw=str(baseline-outage),outage_kwh=str(outage*hours),code='ESTIMATED')
    return row

def apply(state):
    records=state['records'];context=state['context'];cadence=context['cadence_seconds']
    targets=sorted(d for d,m in context['devices'].items() if not m['reference'])
    limits=[r for r in records if r['kind']=='LIMIT']
    samples=defaultdict(list);outages=defaultdict(list);events=defaultdict(list)
    for row in records:
        if row['device'] not in targets:continue
        if row['kind']=='TELEMETRY':samples[row['device']].append(row)
        elif row['kind']=='OUTAGE':outages[row['device']].append(row)
        elif row['kind']=='EVENT':events[row['device']].append(row)
    for device in targets:samples[device].sort(key=lambda r:(instant(r['at']),r['id']))
    unions=union_limits(limits)
    rows=[];slices=[]
    for window in unions:
        boundaries=window_boundaries(window,samples,outages,cadence)
        for a,b in zip(boundaries,boundaries[1:]):
            piece={'start':a.isoformat(),'end':b.isoformat(),'seconds':int((b-a).total_seconds()),
                   'limit_kw':window['limit_kw'],'limit_ids':list(window['limit_ids']),
                   'station_available_kw':None,'station_curtail_kw':None,'curtail_kwh':None,
                   'allocated':False,'reasons':[],'devices':[]}
            device_rows=[]
            for device in targets:
                row=device_slice(device,context['devices'][device]['zone'],a,b,window,samples[device],outages[device],records,context,cadence)
                # 设备级限电事件覆盖的片段限电已由事件口径核算，不再参与限额分摊，整片待查。
                if any(instant(e['start'])<b and a<instant(e['end']) for e in events[device]):
                    row.update(code='LIMIT_EVENT_OVERLAP',baseline_kw=None,outage_kw=None,available_kw=None,outage_kwh=None)
                device_rows.append(row)
            if device_rows and all(r['code']=='ESTIMATED' for r in device_rows):
                total=sum((number(r['available_kw']) for r in device_rows),Decimal(0))
                curtail=max(Decimal(0),total-number(window['limit_kw']));hours=Decimal(piece['seconds'])/3600
                for r in device_rows:
                    share=curtail*number(r['available_kw'])/total if total>0 else Decimal(0)
                    r.update(curtail_kw=str(share),curtail_kwh=str(share*hours),code='ALLOCATED')
                piece.update(station_available_kw=str(total),station_curtail_kw=str(curtail),curtail_kwh=str(curtail*hours),allocated=True)
            else:
                pending=[r for r in device_rows if r['code'] in PENDING_CODES]
                piece.update(reasons=sorted({r['code'] for r in pending}),devices=sorted(r['device'] for r in pending))
                for r in device_rows:
                    if r['code']=='ESTIMATED':r['code']='SLICE_PENDING'
            rows.extend(device_rows);slices.append(piece)
    quant=lambda v:v.quantize(Decimal('.0001'))
    attribution=[]
    for device in targets:
        own=[r for r in rows if r['device']==device]
        attribution.append({'device':device,
            'curtail_kwh':str(quant(sum((number(r['curtail_kwh']) for r in own if r['curtail_kwh'] is not None),Decimal(0)))),
            'outage_kwh':str(quant(sum((number(r['outage_kwh']) for r in own if r['outage_kwh'] is not None),Decimal(0))))})
    station=sum((number(s['curtail_kwh']) for s in slices if s['allocated']),Decimal(0))
    allocated=sum((number(r['curtail_kwh']) for r in rows if r['curtail_kwh'] is not None),Decimal(0))
    outage=sum((number(r['outage_kwh']) for r in rows if r['outage_kwh'] is not None),Decimal(0))
    state['allocation_rows']=rows;state['limit_slices']=slices
    state['limit_unions']=[{'start':w['start'].isoformat(),'end':w['end'].isoformat(),'seconds':int((w['end']-w['start']).total_seconds()),'limit_kw':w['limit_kw'],'limit_ids':w['limit_ids']} for w in unions]
    state['pending_slices']=[{k:s[k] for k in ['start','end','seconds','reasons','devices','limit_ids']} for s in slices if not s['allocated']]
    state['attribution']={'devices':attribution,'station':{'limit_curtail_kwh':str(quant(station)),'allocated_curtail_kwh':str(quant(allocated)),'balanced':quant(station)==quant(allocated)}}
    state['views']['allocate']={'title':'限额分摊','rows':rows,'metrics':{'限额窗口':len(unions),'分摊限电_kWh':str(quant(allocated)),'停运_kWh':str(quant(outage)),'待查片段':len(state['pending_slices'])}}
