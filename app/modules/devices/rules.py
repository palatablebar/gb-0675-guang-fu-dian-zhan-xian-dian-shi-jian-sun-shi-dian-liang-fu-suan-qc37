from app.coerce import number,instant
from app.errors import BusinessError
from app.reasons import reject
from collections import defaultdict

def normalize(row,context):
    if row['kind']=='LIMIT':
        # 场站限额只到场站不到逆变器；指定设备的限额记录视为数据错误。
        if row['device'].strip():raise BusinessError('LIMIT_DEVICE','限额记录不应指定设备')
        start,end=instant(row['start']),instant(row['end'])
        if not start<end or (end-start).total_seconds()>86400:raise BusinessError('INVALID_RANGE','限额区间无效或超过24小时')
        factor={'kW':1,'MW':1000}.get(row['unit'])
        if factor is None:raise BusinessError('BAD_UNIT','功率单位无效')
        limit=number(row['power'])*factor
        if limit<0:raise BusinessError('INVALID_RANGE','限额不能为负')
        return {**row,'start':start.isoformat(),'end':end.isoformat(),'limit_kw':str(limit),'zone':'场站'}
    if row['kind']=='OUTAGE':
        if row['device'] not in context['devices']:raise BusinessError('UNKNOWN_DEVICE','设备不存在')
        device=context['devices'][row['device']]
        start,end=instant(row['start']),instant(row['end'])
        if not start<end or (end-start).total_seconds()>86400:raise BusinessError('INVALID_RANGE','停运区间无效或超过24小时')
        factor={'kW':1,'MW':1000}.get(row['unit'])
        if factor is None:raise BusinessError('BAD_UNIT','功率单位无效')
        capacity=number(row['power'])*factor
        if capacity<0 or capacity>number(device['capacity_kw']):raise BusinessError('INVALID_RANGE','停运容量超出设备额定')
        return {**row,'start':start.isoformat(),'end':end.isoformat(),'outage_kw':str(capacity),'zone':device['zone']}
    if row['device'] not in context['devices']:raise BusinessError('UNKNOWN_DEVICE','设备不存在')
    device=context['devices'][row['device']]
    if row['kind']=='EVENT':
        start,end=instant(row['start']),instant(row['end'])
        if not start<end or (end-start).total_seconds()>86400:raise BusinessError('INVALID_RANGE','限电区间无效或超过24小时')
        return {**row,'start':start.isoformat(),'end':end.isoformat(),'zone':device['zone']}
    if row['kind']!='TELEMETRY':raise BusinessError('BAD_KIND','记录类型无效')
    factor={'kW':1,'MW':1000}.get(row['unit'])
    if factor is None:raise BusinessError('BAD_UNIT','功率单位无效')
    power=number(row['power'])*factor;irr=number(row['irradiance'])
    if power<0 or irr<0 or power>number(device['capacity_kw'])*number('1.2'):raise BusinessError('INVALID_RANGE','功率或辐照度超出范围')
    if row['status'] not in {'健康','故障'}:raise BusinessError('BAD_STATUS','设备状态无效')
    return {**row,'at':instant(row['at']).isoformat(),'power_kw':str(power),'irradiance':str(irr),'zone':device['zone']}
def apply(state):
    good=[]
    for row in state['records']:
        try:good.append(normalize(row,state['context']))
        except BusinessError as e:reject(state,row,e.code,e.message)
    grouped=defaultdict(list)
    for row in good:
        if row['kind']=='TELEMETRY':grouped[(row['device'],row['at'])].append(row)
    bad_ids=set()
    for rows in grouped.values():
        if len(rows)>1:
            identities={(r['power_kw'],r['irradiance'],r['status']) for r in rows}
            victims=rows if len(identities)>1 else sorted(rows,key=lambda r:r['id'])[1:]
            for row in victims:reject(state,row,'SAMPLE_CONFLICT' if len(identities)>1 else 'SAMPLE_DUPLICATE','设备同一时刻存在重复观测');bad_ids.add(row['id'])
    # 限额区间重叠且限额不一致的记录无法取舍，双双隔离待复核。
    limits=[r for r in good if r['kind']=='LIMIT' and r['id'] not in bad_ids]
    for index,a in enumerate(limits):
        for b in limits[index+1:]:
            if instant(a['start'])<instant(b['end']) and instant(b['start'])<instant(a['end']) and a['limit_kw']!=b['limit_kw']:
                for row in (a,b):
                    if row['id'] not in bad_ids:reject(state,row,'LIMIT_CONFLICT','限额区间重叠且限额不一致');bad_ids.add(row['id'])
    state['records']=[r for r in good if r['id'] not in bad_ids]
    state['views']['devices']={'title':'设备与单位','rows':state['records'],'metrics':{'设备':len(state['context']['devices']),'规范记录':len(state['records']),'隔离':len(state['quarantine'])}}
