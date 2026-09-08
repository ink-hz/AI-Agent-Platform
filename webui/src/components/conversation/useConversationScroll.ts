import { useCallback, useLayoutEffect, useRef, useState } from "react";

// Messages scroll independently; the composer stays in the bottom layout row.
export function useConversationScroll(conversationId: string, enabled: boolean) {
  const pageRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLElement | null>(null);
  const followingRef = useRef(true);
  const previousTop = useRef(0);
  const [following, setFollowing] = useState(true);

  const followBottom = useCallback(() => {
    const viewport = viewportRef.current;
    if (!followingRef.current || !viewport || viewport.clientHeight === 0) return;
    viewport.scrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    previousTop.current = viewport.scrollTop;
  }, []);

  const jumpToLatest = useCallback(() => {
    followingRef.current = true;
    setFollowing(true);
    followBottom();
  }, [followBottom]);

  useLayoutEffect(() => {
    if (!enabled) return;
    const page = pageRef.current;
    const viewport = page?.closest<HTMLElement>(".conversation-scroll-region");
    if (!page || !viewport) return;
    viewportRef.current = viewport;
    jumpToLatest();
    const onScroll = () => {
      if (viewport.clientHeight === 0) return;
      const atBottom = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= 48;
      // Content growth is not a request to stop following. Only moving upward
      // away from the bottom pauses it; reaching the bottom resumes it.
      if (atBottom || viewport.scrollTop < previousTop.current) {
        followingRef.current = atBottom;
        setFollowing(atBottom);
      }
      previousTop.current = viewport.scrollTop;
    };
    viewport.addEventListener("scroll", onScroll, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(followBottom);
    observer?.observe(page);
    observer?.observe(viewport);
    return () => {
      viewport.removeEventListener("scroll", onScroll);
      observer?.disconnect();
      viewportRef.current = null;
    };
  }, [conversationId, enabled, followBottom, jumpToLatest]);

  return { pageRef, following, jumpToLatest };
}
