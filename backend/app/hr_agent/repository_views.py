"""Owner-filtered discovery and exact, source-checked result projections."""
from __future__ import annotations
import base64
from datetime import datetime
from uuid import UUID
from app.execution_relay.content_crypto import SealedContent
from .types import HrAgentProblem, ResultQuery, content_sha256, problem, validate_contract

PAGE_SIZE=50

def _uuid(value):
    try:return UUID(str(value))
    except (ValueError,TypeError,AttributeError):raise problem('invalid_input') from None

class RepositoryViewsMixin:
    def _page_cursor(self,owner,query,row,identity):
        payload={'query':query,'at':row['created_at'].isoformat(),'id':str(row[identity])}
        sealed=self.codec.seal_json(f'hr-agent:cursor:{owner}:{content_sha256(query)}',payload)
        return str(sealed.key_version)+'.'+base64.urlsafe_b64encode(sealed.ciphertext).decode()

    def _page_anchor(self,owner,query,cursor):
        if cursor is None:return None
        try:
            if not isinstance(cursor,str) or len(cursor)>4096:raise ValueError()
            version,encoded=cursor.split('.',1)
            sealed=SealedContent(base64.b64decode(encoded,altchars=b'-_',validate=True),int(version))
            value=self.codec.unseal_json(f'hr-agent:cursor:{owner}:{content_sha256(query)}',sealed)
            if set(value)!={'query','at','id'} or value['query']!=query:raise ValueError()
            at=datetime.fromisoformat(value['at'])
            if at.tzinfo is None:raise ValueError()
            return at,_uuid(value['id'])
        except Exception:raise problem('revision_conflict',http_status=409) from None

    @staticmethod
    def _thread_owned(c,owner,thread):
        c.execute('SELECT thread_id FROM platform_hr_agent.threads WHERE owner_id=%s AND thread_id=%s',(owner,thread))
        if c.fetchone() is None:raise problem('not_found',http_status=404)

    def list_threads(self,owner_id,cursor=None):
        owner=_uuid(owner_id);query={'kind':'threads'};anchor=self._page_anchor(owner,query,cursor)
        with self.transaction() as c:
            clause=' AND (created_at,thread_id)>(%s,%s)' if anchor else ''
            c.execute('SELECT * FROM platform_hr_agent.threads WHERE owner_id=%s'+clause+' ORDER BY created_at,thread_id LIMIT %s',(owner,*(anchor or ()),PAGE_SIZE+1))
            rows=c.fetchall();items=[]
            for row in rows[:PAGE_SIZE]:
                # The title was derived from the first input, whose permissions may change.
                c.execute('SELECT i.* FROM platform_hr_agent.inputs i JOIN platform_hr_agent.works w ON w.work_id=i.work_id AND w.owner_id=i.owner_id WHERE w.owner_id=%s AND w.thread_id=%s AND i.revision=1 ORDER BY w.created_at,w.work_id LIMIT 1',(owner,row['thread_id']))
                initial=c.fetchone();title='受限工作'
                if initial:
                    body=self._unseal('inputs',initial['input_id'],'sealed_input',initial)
                    try:
                        self._scope(owner,body['objects'],body['references'],initial['work_id'])
                        for ref in body['references']:
                            if ref['kind']=='result':self._validate_result_sources(c,owner,ref,initial['work_id'])
                    except HrAgentProblem:pass
                    else:title=self._unseal('threads',row['thread_id'],'sealed_title',row)['title']
                items.append({'thread_id':str(row['thread_id']),'title':title,'updated_at':row['created_at'].isoformat()})
            return validate_contract('ThreadPage',{'items':items,'next_cursor':self._page_cursor(owner,query,rows[PAGE_SIZE-1],'thread_id') if len(rows)>PAGE_SIZE else None})

    def list_works(self,owner_id,thread_id,cursor=None):
        owner,thread=_uuid(owner_id),_uuid(thread_id)
        query={'kind':'works','thread_id':str(thread)};anchor=self._page_anchor(owner,query,cursor)
        with self.transaction() as c:
            self._thread_owned(c,owner,thread)
            clause=' AND (created_at,work_id)>(%s,%s)' if anchor else ''
            c.execute('SELECT * FROM platform_hr_agent.works WHERE owner_id=%s AND thread_id=%s'+clause+' ORDER BY created_at,work_id LIMIT %s',(owner,thread,*(anchor or ()),PAGE_SIZE+1))
            rows=c.fetchall()
            return validate_contract('WorkPage',{'items':[self._view(c,row) for row in rows[:PAGE_SIZE]],'next_cursor':self._page_cursor(owner,query,rows[PAGE_SIZE-1],'work_id') if len(rows)>PAGE_SIZE else None})

    def _read_result_row(self,c,owner,row):
        ref={'kind':'result','id':str(row['result_id']),'revision':str(row['revision_id']),'sha256':row['sha256']}
        self._validate_result_sources(c,owner,ref)
        if row['sealed_document'] is None:raise problem('reference_unavailable',http_status=410)
        document=self._unseal('result_revisions',row['revision_id'],'sealed_document',row)
        if content_sha256(document)!=row['sha256']:raise problem('hash_mismatch',http_status=409)
        return validate_contract('ResultView',{**document,'ref':ref,'access_state':'available'})

    def read_result(self,owner_id,result_id,revision):
        owner,result,revision=_uuid(owner_id),_uuid(result_id),_uuid(revision)
        with self.transaction() as c:
            c.execute('SELECT v.*,r.origin_work_id FROM platform_hr_agent.result_revisions v JOIN platform_hr_agent.results r ON r.owner_id=v.owner_id AND r.result_id=v.result_id WHERE v.owner_id=%s AND v.result_id=%s AND v.revision_id=%s',(owner,result,revision))
            row=c.fetchone()
            if row is None:raise problem('not_found',http_status=404)
            return self._read_result_row(c,owner,row)

    def list_results(self,owner_id,query:ResultQuery):
        owner=_uuid(owner_id)
        if not isinstance(query,ResultQuery):raise problem('invalid_input')
        bound={'kind':'results','thread_id':str(_uuid(query.thread_id)) if query.thread_id else None,'object':query.object_ref,'result_kind':query.kind}
        if query.object_ref is not None:validate_contract('ObjectRef',query.object_ref)
        anchor=self._page_anchor(owner,bound,query.cursor)
        with self.transaction() as c:
            params=[owner]
            if query.thread_id:
                self._thread_owned(c,owner,_uuid(query.thread_id))
                scope=' AND EXISTS (SELECT 1 FROM platform_hr_agent.works w WHERE w.owner_id=r.owner_id AND w.work_id=r.origin_work_id AND w.thread_id=%s)';params.append(_uuid(query.thread_id))
            else:
                self._scope(owner,[query.object_ref],[])
                scope=' AND EXISTS (SELECT 1 FROM platform_hr_agent.result_links l WHERE l.owner_id=r.owner_id AND l.result_id=r.result_id AND l.object_kind=%s AND l.object_id=%s)';params.extend((query.object_ref['kind'],query.object_ref['id']))
            if query.kind is not None:scope+=' AND r.kind=%s';params.append(query.kind)
            if anchor:scope+=' AND (r.created_at,r.result_id)>(%s,%s)';params.extend(anchor)
            c.execute('SELECT v.*,r.origin_work_id,r.created_at AS result_created_at FROM platform_hr_agent.results r JOIN platform_hr_agent.result_revisions v ON v.owner_id=r.owner_id AND v.result_id=r.result_id AND v.revision_id=r.current_revision WHERE r.owner_id=%s'+scope+' ORDER BY r.created_at,r.result_id LIMIT %s',(*params,PAGE_SIZE+1))
            rows=c.fetchall();items=[]
            for row in rows[:PAGE_SIZE]:
                try:view=self._read_result_row(c,owner,row)
                except HrAgentProblem as error:
                    if error.problem['code'] in {'not_found','scope_denied','reference_unavailable','dependency_revoked'}:continue
                    raise
                items.append({'ref':view['ref'],'title':view['title'],'objects':view['objects'],'description':'已保存的成果','observed_at':row['created_at'].isoformat(),'state':'available','visibility':{'kind':'private','subject_id':str(owner)},'representation':'authored_text','original_ref':None})
            last=dict(rows[PAGE_SIZE-1],created_at=rows[PAGE_SIZE-1]['result_created_at']) if len(rows)>PAGE_SIZE else None
            return validate_contract('ResultPage',{'items':items,'next_cursor':self._page_cursor(owner,bound,last,'result_id') if last else None})

    def link_result(self,owner_id,result_id,request,key):
        owner,result=_uuid(owner_id),_uuid(result_id);request=validate_contract('LinkResultInput',request)
        with self.transaction() as c:
            c.execute('SELECT * FROM platform_hr_agent.results WHERE owner_id=%s AND result_id=%s FOR UPDATE',(owner,result))
            current=c.fetchone()
            if current is None:raise problem('not_found',http_status=404)
            self._scope(owner,request['objects'],[])
            operation,replay=self._idempotency(c,owner,f'POST /results/{result}/links',key,request)
            c.execute('SELECT v.*,r.origin_work_id FROM platform_hr_agent.result_revisions v JOIN platform_hr_agent.results r ON r.owner_id=v.owner_id AND r.result_id=v.result_id WHERE v.owner_id=%s AND v.result_id=%s AND v.revision_id=%s',(owner,result,_uuid(request['expected_result_revision'])))
            row=c.fetchone()
            if row is None:raise problem('revision_conflict',http_status=409,details={'conflict_kind':'result_revision','current_revision':str(current['current_revision'])})
            view=self._read_result_row(c,owner,row)
            if replay:return replay
            if str(current['current_revision'])!=request['expected_result_revision']:
                raise problem('revision_conflict',http_status=409,details={'conflict_kind':'result_revision','current_revision':str(current['current_revision'])})
            existing_candidates={obj['id'] for obj in view['objects'] if obj['kind']=='candidate'}
            if any(obj['kind']=='candidate' and obj['id'] not in existing_candidates for obj in request['objects']):
                raise problem('scope_denied',http_status=422)
            for obj in request['objects']:
                c.execute('INSERT INTO platform_hr_agent.result_links(owner_id,result_id,object_kind,object_id,linked_by_operation) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(result_id,object_kind,object_id) DO NOTHING',(owner,result,obj['kind'],obj['id'],operation))
            return self._receipt(c,operation,validate_contract('LinkReceipt',{'result_ref':view['ref'],'objects':request['objects']}))
