import asyncio
from datetime import UTC, datetime
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.runtime_dashboard.service import DashboardService

NOW=datetime(2026,10,30,2,tzinfo=UTC)


def run_pages(pages):
    from app.runtime_dashboard.workspaces import voc_business_summary
    app=FastAPI()
    calls=[]
    @app.get('/api/v1/extensions/voc/admin/vocs')
    def page(request:Request,limit:int,cursor:str|None=None):
        assert limit==100 and request.headers['cookie']=='session=real'
        calls.append(cursor)
        value=pages[len(calls)-1] if len(calls)<=len(pages) else pages[-1]
        if isinstance(value,int):
            return JSONResponse({'detail':'failure'},status_code=value)
        return value
    request=Request({'type':'http','scheme':'https','method':'GET','path':'/api/dashboard/overview',
        'root_path':'','server':('agent.example.test',443),'headers':[(b'host',b'agent.example.test'),(b'cookie',b'session=real')],'app':app})
    application=next(a for a in DashboardService(clock=lambda:NOW).overview().applications if a.id=='voc')
    asyncio.run(voc_business_summary(request,application,NOW))
    return application,calls


@pytest.mark.parametrize('pages,count,status',[
    ([{'items':[],'next_cursor':None}],0,'observed'),
    ([{'items':[{'voc_no':'VOC-20261030-001','latest_content':'SECRET'}],'next_cursor':None}],1,'observed'),
    ([{'items':[{'voc_no':'VOC-20261030-001'}],'next_cursor':'next'}, {'items':[{'voc_no':'VOC-20261030-002'}],'next_cursor':None}],2,'observed'),
    ([{'items':[{'voc_no':'VOC-20261030-001'}],'next_cursor':'next'}, {'items':[{'voc_no':'VOC-20261030-001'}],'next_cursor':None}],1,'partial'),
    ([{'items':[{'voc_no':'VOC-20261030-001'}],'next_cursor':'same'}, {'items':[{'voc_no':'VOC-20261030-002'}],'next_cursor':'same'}],2,'partial'),
    ([{'items':[{'voc_no':'VOC-20261030-001'}],'next_cursor':'next'},500],1,'partial'),
    ([{'items':[{'voc_no':'VOC-20261030-001'}],'next_cursor':'next'},403],None,'unavailable'),
    ([401],None,'unavailable'),([500],None,'unavailable'),([302],None,'unavailable'),
])
def test_voc_business_bounded_pagination_and_failure_semantics(pages,count,status):
    app,calls=run_pages(pages)
    metric=app.business_metrics['registered_vocs']
    assert (metric.value,metric.status)==(count,status)
    assert metric.unit=='result'
    assert app.business_sources['registered_vocs'].complete==(status=='observed')
    assert 'SECRET' not in app.model_dump_json() and 'VOC-20261030-001' not in app.model_dump_json()
    assert app.source.source_kind=='voc' and app.usage.retained.turns.value is None


def test_voc_business_does_not_claim_first_thousand_is_total():
    pages=[{'items':[{'voc_no':f'VOC-20261030-{i*100+j:04}'} for j in range(100)],'next_cursor':str(i)} for i in range(11)]
    app,calls=run_pages(pages)
    assert len(calls)==10
    assert app.business_metrics['registered_vocs'].status=='partial'
    assert app.business_metrics['registered_vocs'].value==1000
    assert app.business_metrics['registered_vocs'].reason=='voc_record_limit'
