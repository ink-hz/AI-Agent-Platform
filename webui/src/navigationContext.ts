import { useLayoutEffect, useRef } from "react";

import { currentLocationPath } from "./router";


export type SessionOrigin = {
  path: string;
  scrollY: number;
};

export type SessionOriginState = {
  sessionOrigin: SessionOrigin;
};


function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}


function supportedPlatformPath(path: string): boolean {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("#")) return false;
  try {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || `${url.pathname}${url.search}` !== path) return false;
    return url.pathname === "/"
      || /^\/agents(?:\/[^/]+)?$/.test(url.pathname)
      || /^\/sessions(?:\/[^/]+)?$/.test(url.pathname)
      || url.pathname === "/activity";
  } catch {
    return false;
  }
}


export function sessionOriginFromState(state: unknown): SessionOrigin | null {
  const candidate = record(state).sessionOrigin;
  if (candidate === null || typeof candidate !== "object" || Array.isArray(candidate)) return null;
  const origin = candidate as Record<string, unknown>;
  if (typeof origin.path !== "string" || !supportedPlatformPath(origin.path)) return null;
  if (typeof origin.scrollY !== "number" || !Number.isFinite(origin.scrollY) || origin.scrollY < 0) return null;
  return { path: origin.path, scrollY: origin.scrollY };
}


export function sessionReturnTarget(state: unknown): string | null {
  return sessionOriginFromState(state)?.path ?? null;
}


export function captureSessionOrigin(scrollY: number): SessionOriginState {
  const origin: SessionOrigin = {
    path: currentLocationPath(),
    scrollY: Number.isFinite(scrollY) && scrollY >= 0 ? scrollY : 0,
  };
  const state = { ...record(window.history.state), sessionOrigin: origin } as SessionOriginState;
  window.history.replaceState(state, "", origin.path);
  return state;
}


export function useHistoryScrollRestoration(ready: boolean): void {
  const restored = useRef<string | null>(null);
  const location = currentLocationPath();
  useLayoutEffect(() => {
    if (!ready || restored.current === location) return;
    const origin = sessionOriginFromState(window.history.state);
    if (origin === null || origin.path !== location) return;
    restored.current = location;
    const frame = window.requestAnimationFrame(() => {
      const maximum = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      window.scrollTo(0, Math.min(origin.scrollY, maximum));
    });
    return () => window.cancelAnimationFrame(frame);
  }, [location, ready]);
}
