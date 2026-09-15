/** @vitest-environment jsdom */
import {afterEach,expect,it,vi} from 'vitest';
import {startQrLogin} from './auth';

afterEach(()=>{vi.unstubAllGlobals();window.history.replaceState({},'', '/');});

it.each(['/', '/_preview/dingtalk-r1/'])('refreshes the server challenge before QR admission under %s',async(prefix)=>{
 window.history.replaceState({},'',prefix+'login?return_path=%2Fhr%2F');
 let challenge=false;
 const fetcher=vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
  if(String(input)===prefix+'login'){
   expect(init).toMatchObject({credentials:'include',cache:'no-store'});
   challenge=true;return new Response('<html></html>');
  }
  expect(String(input)).toBe(prefix+'api/v1/auth/dingtalk/start');
  expect(JSON.parse(String(init?.body))).toEqual({return_path:prefix+'hr/'});
  return challenge?new Response(JSON.stringify({authorization_url:'https://login.dingtalk.com/oauth2/auth?state=test'})):new Response('{}',{status:400});
 });
 vi.stubGlobal('fetch',fetcher);
 await expect(startQrLogin('/hr/')).resolves.toContain('https://login.dingtalk.com/');
 challenge=false; // Simulate expiry while the same page remains open.
 await expect(startQrLogin('/hr/')).resolves.toContain('https://login.dingtalk.com/');
 expect(fetcher).toHaveBeenCalledTimes(4);
});

it('does not attempt QR admission if challenge refresh fails',async()=>{
 const fetcher=vi.fn().mockResolvedValue(new Response('{}',{status:503}));vi.stubGlobal('fetch',fetcher);
 await expect(startQrLogin('/hr/')).rejects.toThrow();
 expect(fetcher).toHaveBeenCalledTimes(1);
 expect(fetcher.mock.calls[0][0]).toBe('/login');
});

it('still rejects an authorization URL outside DingTalk',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response('<html/>')).mockResolvedValueOnce(new Response(JSON.stringify({authorization_url:'https://evil.test/'}))));
 await expect(startQrLogin('/hr/')).rejects.toThrow('login response invalid');
});
