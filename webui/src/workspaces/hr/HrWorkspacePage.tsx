import { formatHrIntelligenceReferences, type HrIntelligenceReference } from './hrIntelligenceReference';
import { useCallback, useMemo, useRef, useState } from "react";
import type { Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";
import type { HrComposerDraft, HrKnowledgeSelection, HrInputResultRef } from "../../conversationTypes";
import { createHrApi } from "../../hrApi";
import { createHrR12Api } from "../../hrR12Api";
import type { HrPositionSection } from "../../hrR12Types";
import type { HrPosition } from "../../hrTypes";
import { directConversationPath } from "../../platform/workspaces";
import { navigate } from "../../router";
import { WorkspaceErrorBoundary } from "../../shared/WorkspaceErrorBoundary";
import { DirectAgentWorkspace, type DirectAgentDraftSnapshot } from "../direct/DirectAgentWorkspace";
import { HrPanoramaWorkspace } from "./HrPanoramaWorkspace";
import { HrPositionDetailsDrawer, type HrPositionDetailsTab } from "./HrPositionDetailsDrawer";
import { HrPositionActions } from "./HrPositionActions";
import { HrPositionPicker } from "./HrPositionPicker";
import { HrKnowledgePanel } from "./HrKnowledgePanel";
import { HrPositionIndex } from "./HrPositionIndex";
import { HrWorkspaceShell } from "./HrWorkspaceShell";
import { HrTurnResults, HrInputResults, useHrConversationResults } from "./HrTurnResults";
import { useHrChatPosition } from "./useHrChatPosition";
function chatPath(id:string){return directConversationPath('hr-bot',id)??`/hr/conversations/${encodeURIComponent(id)}`;}
export function HrWorkspacePage(props:{account:Account;conversationId?:string;positionId?:string;section?:HrPositionSection;freeChat?:boolean;positions?:boolean;panorama?:boolean;panoramaReportId?:string}){
  return <HrWorkspaceSession key={props.account.internal_user_id} {...props}/>;
}
function HrWorkspaceSession(props:Parameters<typeof HrWorkspacePage>[0]){
  const api=useMemo(()=>createHrApi(props.account.csrf_token),[props.account.csrf_token]);
  const r12=useMemo(()=>createHrR12Api(props.account.csrf_token),[props.account.csrf_token]);
  const lastChat=useRef<string|undefined>(undefined);
  const away=Boolean(props.positions||props.panorama||props.panoramaReportId||props.positionId);
  if(!away)lastChat.current=props.conversationId;
  const conversationId=away?lastChat.current:props.conversationId;
  const chatHref=conversationId?chatPath(conversationId):'/hr/';
  const panoramaActive=Boolean(props.panorama||props.panoramaReportId);
  const panoramaVisited=useRef(false);if(panoramaActive)panoramaVisited.current=true;
  const [intelligenceReferences,setIntelligenceReferences]=useState<HrIntelligenceReference[]>([]);
  const intelligenceRef=useRef(intelligenceReferences);intelligenceRef.current=intelligenceReferences;
  const [intelligenceError,setIntelligenceError]=useState('');
  const selectIntelligence=useCallback((reference:HrIntelligenceReference)=>{
    const current=intelligenceRef.current;
    const next=current.some(item=>item.key===reference.key)?current:[...current,reference];
    try{formatHrIntelligenceReferences(next);}catch{setIntelligenceError('所选情报超过 12 KiB，请先移除部分引用。');return;}
    setIntelligenceReferences(next);setIntelligenceError('');navigate(chatHref);
  },[chatHref]);
  const removeIntelligence=useCallback((key:string)=>{setIntelligenceReferences(items=>items.filter(item=>item.key!==key));setIntelligenceError('');},[]);
  const clearIntelligence=useCallback((keys:readonly string[])=>{const sent=new Set(keys);setIntelligenceReferences(items=>items.filter(item=>!sent.has(item.key)));},[]);
  const [selectedPosition,setSelectedPosition]=useState<HrPosition|null>(null);
  const [candidateIds,setCandidateIds]=useState<string[]>([]);
  const [candidateAttachments,setCandidateAttachments]=useState<string[]>([]);
  const [drawerOpen,setDrawerOpen]=useState(false);
  const [drawerTab,setDrawerTab]=useState<HrPositionDetailsTab>('position');
  const [knowledgeOpen,setKnowledgeOpen]=useState(false);
  const [knowledge,setKnowledge]=useState<HrKnowledgeSelection[]>([]);
  const [inputResults,setInputResults]=useState<HrInputResultRef[]>([]);
  const [composerDraft,setComposerDraft]=useState<HrComposerDraft>();
  const [revision,setRevision]=useState(0);
  const [draftError,setDraftError]=useState('');
  const draft=useRef<DirectAgentDraftSnapshot|undefined>(undefined);
  const retainDraft=useCallback((value:DirectAgentDraftSnapshot)=>{draft.current=value;},[]);
  const settled=useCallback(()=>setRevision(value=>value+1),[]);
  const position=useHrChatPosition({positionId:selectedPosition?.positionId,api,r12,readOnly:props.account.hard_stale_read_only,revision});
  const results=useHrConversationResults(conversationId,revision);
  function choose(value:HrPosition|null){if(inputResults.length)setDraftError("已切换岗位，原成果引用已移除。");setInputResults([]);setSelectedPosition(value);setCandidateIds([]);setCandidateAttachments([]);setDrawerOpen(false);}
  async function fill(value:HrComposerDraft,positionId:string|null){
    setDraftError('');
    if(positionId!==selectedPosition?.positionId){
      try {choose(positionId?await api.position(positionId):null);}catch{setDraftError('无法读取该成果的岗位，请稍后重试。');return;}
    }
    if(value.standardConsent)setIntelligenceReferences([]);
    setInputResults(value.inputResults??[]);
    setCandidateIds(value.positionCandidateIds??[]);setCandidateAttachments(value.attachmentIds??[]);
    setComposerDraft({...value,positionId});setDrawerOpen(false);navigate(chatHref);
  }
  return <HrWorkspaceShell account={props.account} chatHref={chatHref} current={props.panorama||props.panoramaReportId?'panorama':props.positions||props.positionId?'positions':'chat'} onOpenKnowledge={()=>setKnowledgeOpen(true)}>
    <div className="hr-workspace-chat-panel" hidden={away}><WorkspaceErrorBoundary title="HR 智能工作台"><DirectAgentWorkspace
      account={props.account} agentId="hr-bot" autoFocusComposer conversationId={conversationId} conversationPath={chatPath}
      createdConversationPath={chatPath} key={`hr-chat:${props.account.internal_user_id}`} layout="standard"
      workspaceLabel="HR 智能工作台" workspaceMark="HR" workspaceRootPath="/hr/" showTaskStarters={false} showWorkspaceBackLink={false}
      header={<>{position.status}{draftError&&<p role="alert">{draftError}</p>}{results.error&&<p role="alert">{results.error}</p>}</>} initialDraftSnapshot={draft.current} onDraftSnapshotChange={retainDraft} onConversationSettled={settled}
      positionMaterialIds={position.detail?.materialAttachmentIds} positionArtifactAttachmentIds={position.detail?.artifactAttachmentIds}
      onPositionMaterialChange={selectedPosition&&!props.account.hard_stale_read_only?position.changeMaterial:undefined}
      turnScope={{positionId:selectedPosition?.positionId??null,positionCandidateIds:candidateIds,attachmentIds:[...new Set([...position.selectedMaterialIds,...candidateAttachments])]}}
      composerDraft={composerDraft} inputResults={inputResults} onInputResultsSubmitted={ids=>setInputResults(items=>items.filter(r=>!ids.includes(r.resultId)))}
      intelligenceReferences={intelligenceReferences} onRemoveIntelligenceReference={removeIntelligence} onIntelligenceReferencesSubmitted={clearIntelligence}
      composerTools={()=><><HrInputResults items={inputResults} onRemove={id=>setInputResults(items=>items.filter(r=>r.resultId!==id))}/><HrPositionPicker api={api} selected={selectedPosition} disabled={props.account.hard_stale_read_only} existingConversation={Boolean(conversationId)} onSelect={choose}
        onOpenDetails={selectedPosition?()=>{setDrawerTab('position');setDrawerOpen(true);}:undefined}/>{position.materialsControl}
        {selectedPosition&&<HrPositionActions title={selectedPosition.title} readOnly={props.account.hard_stale_read_only}
          onDraft={text=>void fill({id:crypto.randomUUID(),text},selectedPosition.positionId)}
          onCandidates={()=>{setDrawerTab('candidates');setDrawerOpen(true);}}/>}
        {candidateIds.length>0&&<button type="button" onClick={()=>{setCandidateIds([]);setCandidateAttachments([]);setInputResults([]);}}>已选 {candidateIds.length} 位候选人 ×</button>}
        </>}
      newConversationHeader={<section className="hr-conversation-welcome"><span>AI 招聘协作</span><h1>{selectedPosition?.title??'今天想推进哪项招聘工作？'}</h1><p>搜索选择岗位，直接提出要求。切换岗位只影响下一轮，同一段对话可以一直使用。</p></section>}
      selectedKnowledgeResources={knowledge} onKnowledgeResourcesSubmitted={()=>setKnowledge([])} onRemoveKnowledgeResource={id=>setKnowledge(items=>items.filter(item=>item.id!==id))}
      renderTurnContext={turnId=><HrTurnResults turnId={turnId} data={results} readOnly={props.account.hard_stale_read_only} onDraft={(value,id)=>void fill(value,id)}/>}
    /></WorkspaceErrorBoundary></div>
    {props.positionId?<main className="hr-position-state"><h1>岗位工作页已合并到主对话</h1><p>在输入框旁搜索岗位即可继续，已有消息与材料保留。</p><PlatformLink href={props.conversationId?chatPath(props.conversationId):chatHref}>返回对话</PlatformLink></main>
      :props.positions?<HrPositionIndex account={props.account} onSelect={value=>{choose(value);navigate(chatHref);}}/>:null}
    {panoramaVisited.current&&<div className="hr-workspace-panorama-panel" hidden={!panoramaActive} aria-hidden={!panoramaActive?"true":undefined}><WorkspaceErrorBoundary title="全景分析"><HrPanoramaWorkspace account={props.account} insightVersionId={props.panoramaReportId} onSelectReference={selectIntelligence}/>{intelligenceError&&<p role="alert">{intelligenceError}</p>}</WorkspaceErrorBoundary></div>}
    {selectedPosition&&<HrPositionDetailsDrawer key={selectedPosition.positionId} api={r12} csrfToken={props.account.csrf_token} detail={position.detail??selectedPosition} open={drawerOpen} onClose={()=>setDrawerOpen(false)} readOnly={props.account.hard_stale_read_only}
      currentContextVersionId={position.context?.contextVersionId??null} contextRefreshGeneration={position.refreshGeneration} resourceRefreshGeneration={position.refreshGeneration}
      activeTab={drawerTab} onActiveTabChange={setDrawerTab} onConfirmed={position.confirmContext}
      onCandidateDraft={(text,ids,attachments)=>{setInputResults([]);setCandidateIds(ids);setCandidateAttachments(attachments);setComposerDraft({id:crypto.randomUUID(),text});setDrawerOpen(false);}}/>}
    {knowledgeOpen&&<HrKnowledgePanel onClose={()=>setKnowledgeOpen(false)} onSelect={selection=>{setKnowledge([selection]);setKnowledgeOpen(false);navigate(chatHref);}}/>}
  </HrWorkspaceShell>;
}
