import { useState } from 'react';
import { platformPath } from '../../auth';
export function HrChannelLink({csrfToken,disabled}:{csrfToken:string;disabled:boolean}) {
  const [command,setCommand]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  async function update(method:'POST'|'DELETE') {
    setBusy(true);setError('');
    try {
      const response=await fetch(platformPath('/api/v1/hr/channel-link'),{method,credentials:'include',headers:{'X-CSRF-Token':csrfToken}});
      if(!response.ok)throw new Error();const value=await response.json();setCommand(method==='POST'?value.command:'');
      if(method==='DELETE')setError('已解除飞书关联。');
    }catch{setError('暂时无法更新关联，请稍后重试。');}finally{setBusy(false);}
  }
  return <details className="hr-next-turn-options"><summary>关联飞书</summary><div><p>在 Hannah 飞书私聊发送以下指令，将使用当前账号的岗位范围与确认标准。指令 10 分钟有效，请勿转发。</p>
    <button type="button" disabled={disabled||busy} onClick={()=>void update('POST')}>生成关联指令</button>
    {command&&<input aria-label="飞书关联指令" readOnly value={command} onFocus={event=>event.target.select()}/>}
    <button type="button" disabled={disabled||busy} onClick={()=>void update('DELETE')}>解除此账号的飞书关联</button>
    {error&&<p role="status">{error}</p>}
  </div></details>;
}
