import { useState } from "react";

import { inClientLogin, inClientLoginAvailable, platformPath, startQrLogin } from "../auth";


export interface LoginPageProps {
  onStartQr?: () => Promise<string>;
  onInClient?: () => Promise<void>;
  onNavigate?: (target: string) => void;
}


export function LoginPage({
  onStartQr = () => startQrLogin(),
  onInClient,
  onNavigate = (target) => window.location.assign(target),
}: LoginPageProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(() => new URLSearchParams(window.location.search).has("error"));
  const inClientAction = onInClient ?? (inClientLoginAvailable() ? inClientLogin : null);
  const begin = async () => {
    setBusy(true);
    setError(false);
    try {
      onNavigate(await onStartQr());
    } catch {
      setBusy(false);
      setError(true);
    }
  };
  const beginInClient = async () => {
    if (!inClientAction) return;
    setBusy(true);
    setError(false);
    try {
      await inClientAction();
      onNavigate(platformPath("/account"));
    } catch {
      setBusy(false);
      setError(true);
    }
  };
  return (
    <main className="login-shell">
      <section className="login-card" aria-labelledby="login-title">
        <img src="./favicon.ico" alt="" aria-hidden="true" />
        <p className="login-kicker">ORBBEC INTERNAL</p>
        <h1 id="login-title">Agent Platform</h1>
        <p>使用奥比中光钉钉企业身份进入。平台不提供用户名或密码登录。</p>
        {error && <div className="auth-message is-error" role="alert">
          <strong>登录未完成</strong>
          <span>请重新发起钉钉登录；若问题持续，请联系平台所有者。</span>
        </div>}
        <button type="button" disabled={busy} onClick={() => void begin()}>
          {busy ? "正在打开钉钉…" : "钉钉扫码登录"}
        </button>
        {inClientAction && <button className="secondary-login" type="button" disabled={busy} onClick={() => void beginInClient()}>
          钉钉内免登
        </button>}
        <small>在钉钉客户端内打开时，将使用企业免登流程。</small>
      </section>
    </main>
  );
}
