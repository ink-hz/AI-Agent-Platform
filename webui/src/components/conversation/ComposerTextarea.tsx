import { useLayoutEffect, useRef, type TextareaHTMLAttributes } from "react";

export function ComposerTextarea({ autoSize = false, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement> & { autoSize?: boolean }) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const resize = () => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.max(48, Math.min(200, input.scrollHeight))}px`;
  };
  useLayoutEffect(() => {
    if (autoSize) resize();
  }, [autoSize, props.value]);
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!autoSize || !input || typeof ResizeObserver === "undefined") return;
    let width = input.clientWidth;
    const observer = new ResizeObserver(() => {
      if (input.clientWidth === width) return;
      width = input.clientWidth;
      resize();
    });
    observer.observe(input);
    return () => observer.disconnect();
  }, [autoSize]);
  return <textarea {...props} ref={inputRef} />;
}
