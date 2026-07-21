import type { AnchorHTMLAttributes, MouseEvent } from "react";

import { navigate } from "../router";


export function PlatformLink(props: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const { href = "/", onClick, ...rest } = props;
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(href);
  };
  return <a {...rest} href={href} onClick={follow} />;
}
