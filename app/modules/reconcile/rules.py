from decimal import Decimal
from app.coerce import number

def reconcile(state):
    known=[r for r in state['energy_rows'] if r['loss_kwh'] is not None];unknown=[r for r in state['energy_rows'] if r['loss_kwh'] is None]
    sums={k:sum((number(r[k]) for r in known),Decimal(0)) for k in ['loss_kwh','actual_kwh','baseline_kwh']}
    union_seconds=sum(r['seconds'] for r in state['event_unions']);slice_seconds=sum(r['seconds'] for r in state['energy_rows']);c=state['counts'];explained=len(state['records'])+len(state['quarantine'])+c['blank']+c['duplicate']+c['repeated_header']
    # Decimal周期小数允许最后一位精度误差，展示前统一量化到0.0001kWh再检查。
    quant=lambda v:v.quantize(Decimal('.0001'))
    slices=state.get('limit_slices',[]);alloc=state.get('allocation_rows',[])
    limit_curtail=sum((number(s['curtail_kwh']) for s in slices if s['allocated']),Decimal(0))
    allocated=sum((number(r['curtail_kwh']) for r in alloc if r['curtail_kwh'] is not None),Decimal(0))
    outage=sum((number(r['outage_kwh']) for r in alloc if r['outage_kwh'] is not None),Decimal(0))
    pending=[s for s in slices if not s['allocated']]
    return {'input':c['input'],'accepted':len(state['records']),'quarantined':len(state['quarantine']),'row_balanced':explained==c['input'],'union_seconds':union_seconds,'slice_seconds':slice_seconds,'time_balanced':union_seconds==slice_seconds,'loss_kwh':str(quant(sums['loss_kwh'])),'actual_kwh':str(quant(sums['actual_kwh'])),'baseline_kwh':str(quant(sums['baseline_kwh'])),'energy_balanced':quant(sums['baseline_kwh'])==quant(sums['actual_kwh']+sums['loss_kwh']),'unknown_segments':len(unknown),'unknown_seconds':sum(r['seconds'] for r in unknown),'limit_curtail_kwh':str(quant(limit_curtail)),'allocated_curtail_kwh':str(quant(allocated)),'outage_kwh':str(quant(outage)),'limit_balanced':quant(limit_curtail)==quant(allocated),'pending_segments':len(pending),'pending_seconds':sum(s['seconds'] for s in pending)}
def apply(state):
    state['balance']=reconcile(state);state['views']['reconcile']={'title':'时间与能量四账','rows':[state['balance']],'metrics':{'确定损失_kWh':state['balance']['loss_kwh'],'限额限电_kWh':state['balance']['limit_curtail_kwh'],'未定分钟':state['balance']['unknown_seconds']//60,'待查片段':state['balance']['pending_segments'],'并集分钟':state['balance']['union_seconds']//60}}
