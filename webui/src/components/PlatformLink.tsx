import type { AnchorHTMLAttributes, MouseEvent } from "react";

import { captureSessionOrigin } from "../navigationContext";
import { navigate } from "../router";


type PlatformLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  preserveSessionContext?: boolean;
};


export function PlatformLink(props: PlatformLinkProps) {
  const { href = "/", onClick, preserveSessionContext = false, ...rest } = props;
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const state = preserveSessionContext ? captureSessionOrigin(window.scrollY) : undefined;
    navigate(href, { state });
  };
  return <a {...rest} href={href} onClick={follow} />;
}
