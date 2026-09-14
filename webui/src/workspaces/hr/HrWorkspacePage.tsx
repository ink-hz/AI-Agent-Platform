import {useMemo} from 'react';
import type {Account} from '../../auth';
import {createHrApi} from '../../hrApi';
import {createHrR12Api} from '../../hrR12Api';
import type {HrPositionSection} from '../../hrR12Types';
import {WorkspaceErrorBoundary} from '../../shared/WorkspaceErrorBoundary';
import {HrPositionWorkflow} from './HrPositionWorkflow';
import {HrPositionIndex} from './HrPositionIndex';
import {HrPanoramaWorkspace} from './HrPanoramaWorkspace';
import {HrWorkspaceShell} from './HrWorkspaceShell';
import {openHrWork} from './hrCloudLaunch';
export function HrWorkspacePage(props:{account:Account;positionId?:string;section?:HrPositionSection;positions?:boolean;panorama?:boolean}) {
 const {account}=props;
 const api=useMemo(()=>createHrApi(account.csrf_token),[account.csrf_token]);
 const r12=useMemo(()=>createHrR12Api(account.csrf_token),[account.csrf_token]);
 return <HrWorkspaceShell account={account} current={props.panorama?'panorama':'positions'}>
  <WorkspaceErrorBoundary key={`${account.internal_user_id}:${account.csrf_token}`} title={props.panorama?'HR 情报':'岗位'}>
   {props.panorama?<HrPanoramaWorkspace account={account}/>:props.positionId?<HrPositionWorkflow key={props.positionId} account={account} positionId={props.positionId} section={props.section} api={api} r12={r12} onDraft={(value,id)=>openHrWork(account.internal_user_id,id,value.text)}/>:<HrPositionIndex account={account} api={api} onSelect={position=>openHrWork(account.internal_user_id,position.positionId)}/>}
  </WorkspaceErrorBoundary>
 </HrWorkspaceShell>;
}
