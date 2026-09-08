import { useCallback, useEffect, useRef, useState } from "react";
import type { ConversationAttachment } from "../../conversationTypes";
import type { HrApi } from "../../hrApi";
import type { HrR12Api } from "../../hrR12Api";
import type { HrContextVersion, HrPositionMaterialItem } from "../../hrR12Types";
import type { HrPositionDetail } from "../../hrTypes";
import { completeMutationRequest, retainMutationRequest } from "./hrMutationRequest";

// Selection belongs to the next message. This hook never starts an execution.
export function useHrChatPosition({positionId,api,r12,readOnly,revision=0}:{
  positionId?:string;api:HrApi;r12:HrR12Api;readOnly:boolean;revision?:number;
}) {
  const [loaded,setLoaded]=useState<{positionId:string;detail:HrPositionDetail;context:HrContextVersion|null;materials:HrPositionMaterialItem[]}|null>(null);
  const [selectedMaterialIds,setSelectedMaterialIds]=useState<string[]>([]);
  const [notice,setNotice]=useState<string|null>(null);
  const [working,setWorking]=useState(false);
  const [generation,setGeneration]=useState(0);
  const selected=useRef(positionId);selected.current=positionId;
  const data=loaded?.positionId===positionId?loaded:null;
  const refresh=useCallback(()=>setGeneration(value=>value+1),[]);
  useEffect(()=>{setSelectedMaterialIds([]);setNotice(null);},[positionId]);
  useEffect(()=>{
    if(!positionId){setLoaded(null);return;}
    const controller=new AbortController();
    void Promise.all([api.position(positionId,controller.signal),r12.context(positionId,controller.signal),r12.resources(positionId,controller.signal)])
      .then(([detail,context,resources])=>{if(controller.signal.aborted)return;
        const materials=resources.materials.filter(item=>item.state==='ready'&&item.downloadAvailable);
        setLoaded({positionId,detail,context:context.current,materials});setNotice(null);
        setSelectedMaterialIds(ids=>ids.filter(id=>materials.some(item=>item.attachmentId===id)));
      }).catch(()=>{if(!controller.signal.aborted)setNotice('岗位资料暂时无法读取。');});
    return ()=>controller.abort();
  },[api,r12,positionId,generation,revision]);
  async function changeMaterial(attachment:ConversationAttachment,active:boolean){
    if(!positionId||readOnly||working)return;
    setWorking(true);
    const operation=retainMutationRequest(`position-material:${positionId}:${active?'promote':'remove'}`,{attachmentId:attachment.attachmentId});
    try{
      if(active)await api.promoteMaterial(positionId,attachment.attachmentId,operation.requestId);
      else await api.removeMaterial(positionId,attachment.attachmentId,operation.requestId);
      completeMutationRequest(operation.key);if(selected.current===positionId)refresh();
    }catch{if(selected.current===positionId)setNotice('岗位材料操作未完成，请重试。');}
    finally{setWorking(false);}
  }
  const materialsControl=data&&data.materials.length>0?<details className="hr-next-turn-options"><summary>岗位材料{selectedMaterialIds.length?` · ${selectedMaterialIds.length}`:''}</summary><div>
    {data.materials.map(item=><label key={item.attachmentId}><input type="checkbox" disabled={readOnly} checked={selectedMaterialIds.includes(item.attachmentId)}
      onChange={event=>setSelectedMaterialIds(ids=>event.target.checked?[...ids,item.attachmentId]:ids.filter(id=>id!==item.attachmentId))}/>{item.filename}</label>)}
  </div></details>:null;
  return {detail:data?.detail,context:data?.context,working,selectedMaterialIds,materialsControl,refreshGeneration:generation+revision,changeMaterial,
    status:notice?<p role="status">{notice}<button type="button" onClick={refresh}>重新读取</button></p>:null,
    confirmContext:refresh};
}
