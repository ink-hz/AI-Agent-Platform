"""Verified private-channel identities reuse Web owners and the existing turn intake."""
import hashlib
import json
import re
import secrets
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from pydantic import Field
from app.execution_relay.contracts_v6 import StrictValue, HrTurnScope
from app.agent_brain.conversation_models import ConversationTurnSubmission
from app.agent_brain.conversation_repository import ConversationRepositoryError
from .standard_consent import StandardConsent
from .tool_service import HrToolError


class ChannelMessage(StrictValue):
    tenant_id: str = Field(alias='tenantId',min_length=1,max_length=256)
    app_id: str = Field(alias='appId',min_length=1,max_length=256)
    open_id: str = Field(alias='openId',min_length=1,max_length=512)
    chat_id: str = Field(alias='chatId',min_length=1,max_length=512)
    message_id: str = Field(alias='messageId',min_length=1,max_length=512)
    text: str = Field(min_length=1,max_length=32768)


def key(parts):
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class ChannelIdentityRequired(PermissionError):
    pass


class HrChannelIdentityService:
    def __init__(self, tools, commands, authorization):
        self.tools,self.commands,self.authorization=tools,commands,authorization

    def code(self, owner):
        code=secrets.token_urlsafe(24)
        with self.tools.repository._connection() as c:
            c.execute('delete from platform_hr.channel_link_codes_v6 where owner_internal_user_id=%s or expires_at<clock_timestamp()',(owner,))
            c.execute("insert into platform_hr.channel_link_codes_v6(code_hash,owner_internal_user_id,expires_at) values(%s,%s,clock_timestamp()+interval '10 minutes')",(key([code]),owner))
        return {'command':'/关联 '+code,'expiresInSeconds':600}

    def unlink(self,owner):
        with self.tools.repository._connection() as c:
            c.execute('delete from platform_hr.channel_identities_v6 where owner_internal_user_id=%s',(owner,))
            c.execute('delete from platform_hr.channel_link_codes_v6 where owner_internal_user_id=%s',(owner,))
        return {'status':'ok'}

    def handle(self,worker,message):
        identity=key([worker,message.tenant_id,message.app_id,message.open_id])
        text=message.text
        with self.tools.repository._connection() as c:
            # Same sender linking and ordinary requests serialize at the identity boundary.
            c.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',(identity,))
            row=c.execute('select owner_internal_user_id from platform_hr.channel_identities_v6 where identity_key=%s',(identity,)).fetchone()
            if text.startswith('/关联 '):
                code=text.removeprefix('/关联 ')
                if re.fullmatch('[A-Za-z0-9_-]{32}',code) is None:raise ValueError('关联码无效')
                ticket=c.execute('select * from platform_hr.channel_link_codes_v6 where code_hash=%s and expires_at>clock_timestamp() for update',(key([code]),)).fetchone()
                if ticket is None or ticket['consumed_by'] not in (None,identity):raise PermissionError('关联码无效或已过期，请在 HR 网页重新生成')
                if row and row['owner_internal_user_id']!=ticket['owner_internal_user_id']:raise PermissionError('此飞书账号已有绑定，请先从原平台账号解除关联')
                owner=ticket['owner_internal_user_id']
                if not self.authorization.decide_for_user_id(owner,'hr-bot').allowed:raise PermissionError('平台账号没有 HR 权限')
                c.execute('insert into platform_hr.channel_identities_v6(identity_key,owner_internal_user_id) values(%s,%s) on conflict do nothing',(identity,owner))
                c.execute('update platform_hr.channel_link_codes_v6 set consumed_by=%s where code_hash=%s',(identity,key([code])))
                return {'text':'账号已关联。发送“J编号 你的要求”即可使用同一岗位标准；发送“/标准 J编号”查看当前标准。建议与确认在同一平台对话保留。'}
            if row is None:raise ChannelIdentityRequired('私有岗位标准需要关联身份，请在 HR 网页的“关联飞书”生成指令，并在这里发送。通用招聘分析可在网页直接进行。')
            owner=row['owner_internal_user_id']
            if not self.authorization.decide_for_user_id(owner,'hr-bot').allowed:raise PermissionError('平台账号没有 HR 权限')
            consent=None
            chat_key=uuid5(NAMESPACE_URL,'hr-channel:'+identity+':'+message.chat_id)
            if text.startswith('/确认 '):
                parts=text.split(' ')
                if len(parts)!=3:raise ValueError('请使用 /确认 建议编号 条目编号,条目编号')
                shown=c.execute('select presented_at from platform_hr.result_presentations_v6 where receipt_id=%s and owner_internal_user_id=%s',(UUID(parts[1]),owner)).fetchone()
                if shown is None:raise PermissionError('请先查看建议，再确认具体条目')
                proposal=self.tools.result(owner,UUID(parts[1]))
                result=proposal['result']
                if result['schemaId']!='hr.standard-proposal.v1':raise ValueError('这不是岗位标准建议')
                receipt=c.execute('select content_sha256 from platform_hr.tool_operations_v6 where receipt_id=%s and owner_internal_user_id=%s',(UUID(parts[1]),owner)).fetchone()
                consent=StandardConsent(proposalResultId=UUID(parts[1]),proposalContentSha256=receipt['content_sha256'],expectedContextVersionId=UUID(result['baseContextVersionId']) if result['baseContextVersionId'] else None,selectedChangeIds=tuple(parts[2].split(',')))
                if not consent.accepts_text(text):raise ValueError('确认指令无效')
                position_id=UUID(proposal['positionId']);conversation_id=UUID(proposal['conversationId'])
            elif text.startswith('/建议 '):
                proposal=self.tools.result(owner,UUID(text.removeprefix('/建议 ')))
                result=proposal['result']
                if result['schemaId']!='hr.standard-proposal.v1':raise ValueError('这不是岗位标准建议')
                return {'text':result['title']+'\n\n'+'\n\n'.join(item['changeId']+'：'+item['markdown'] for item in result['changes'])+'\n\n发送 /确认 '+text.removeprefix('/建议 ')+' 条目编号,条目编号（只选择认可条目）。'}
            else:
                match=re.match(r'^(?:/标准 )?(J\d+)(?:\s|$)',text,re.I)
                if not match:raise ValueError('请在要求前写明岗位编号，例如“J11014 请校准需求”；查看标准使用“/标准 J11014”。')
                position=c.execute("select p.position_id,p.current_context_version_id,to_jsonb(v) as standard from platform_hr.positions p left join platform_hr.position_context_versions v on v.context_version_id=p.current_context_version_id and v.owner_internal_user_id=p.owner_internal_user_id and v.position_id=p.position_id where p.owner_internal_user_id=%s and p.official_job_id=%s and p.internal_status='active'",(owner,match[1].upper())).fetchone()
                if position is None:raise PermissionError('在你的岗位范围内找不到这个编号')
                if text.startswith('/标准 '):
                    value=position['standard']
                    if position['current_context_version_id'] and (not value or value['state']!='confirmed'):raise ValueError('当前标准版本不一致')
                    return {'text':('当前确认标准 '+str(value['context_version_id'])+'\n'+json.dumps(value['modules'],ensure_ascii=False,indent=2)) if value else '这个岗位尚无共同确认的标准。'}
                position_id=position['position_id'];conversation_id=None
        # Reuse existing shell/turn idempotency and admission. No channel execution store.
        if conversation_id is None:
            conversation=self.commands.ensure_direct_conversation_shell(owner,chat_key,direct_agent_id='hr-bot',title='飞书招聘协作')
            conversation_id=conversation.conversation_id
        request_id=uuid5(NAMESPACE_URL,'hr-channel-message:'+identity+':'+message.message_id)
        submission=ConversationTurnSubmission(text,hr_scope=HrTurnScope(positionId=position_id,positionCandidateIds=(),attachmentIds=()),standard_consent=consent,trusted_channel_origin={'provider':'feishu','workerId':worker,**message.model_dump(mode='json',by_alias=True,exclude={'text'})})
        submitted=self.commands.append_turn(owner,conversation_id,request_id,submission)
        return {'text':'已在统一对话受理，可查看进度、成果和确认条目：','conversationId':str(submitted.conversation.conversation_id),'turnId':str(submitted.turn.turn_id)}


def build_channel_user_router(service,require_hr_access):
    router=APIRouter()
    @router.post('/api/v1/hr/channel-link')
    def code(owner: UUID=Depends(require_hr_access)):
        return JSONResponse(service.code(owner),headers={'Cache-Control':'no-store'})
    @router.delete('/api/v1/hr/channel-link')
    def unlink(owner: UUID=Depends(require_hr_access)):
        return JSONResponse(service.unlink(owner),headers={'Cache-Control':'no-store'})
    return router


def attach_channel_worker_route(router,authenticated,service):
    @router.post('/hr/v6/channel-messages')
    async def message(request:Request):
        auth=await authenticated(request)
        if isinstance(auth,JSONResponse):return auth
        if 'hr-bot' not in auth.identity.allowed_agent_ids:raise HTTPException(403,'HR channel forbidden')
        try:
            body=ChannelMessage.model_validate_json(auth.body)
            return JSONResponse(await run_in_threadpool(service.handle,auth.identity.worker_id,body),headers={'Cache-Control':'no-store'})
        except ChannelIdentityRequired as error:
            return JSONResponse({'code':'identity_required','detail':str(error)},status_code=403,headers={'Cache-Control':'no-store'})
        except PermissionError as error:raise HTTPException(403,str(error)) from None
        except (ValueError,HrToolError) as error:raise HTTPException(400,str(error) if not isinstance(error,ValueError) else 'HR 渠道请求无效，请检查指令与岗位编号') from None
        except ConversationRepositoryError:raise HTTPException(409,'此对话已有处理中任务，或当前无法受理；请在网页查看进度') from None
