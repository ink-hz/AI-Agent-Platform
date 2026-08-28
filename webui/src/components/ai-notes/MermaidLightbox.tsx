import { useEffect, useId, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { WheelEvent as ReactWheelEvent } from "react";


type MermaidLightboxProps = {
  imageSource: string;
  title: string;
  description: string | null;
  onClose: () => void;
};

type Point = { x: number; y: number };
type DragState = Point & {
  pointerId: number;
  originX: number;
  originY: number;
  moved: boolean;
};

const ORIGIN: Point = { x: 0, y: 0 };


export function MermaidLightbox({ imageSource, title, description, onClose }: MermaidLightboxProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef(false);
  const descriptionId = useId();
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState<Point>(ORIGIN);

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      document.body.style.overflow = previousOverflow;
      if (dialog?.open) dialog.close();
    };
  }, []);

  function wheel(event: ReactWheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const delta = event.deltaY < 0 ? .25 : -.25;
    setScale((current) => {
      const next = Math.min(4, Math.max(1, Number((current + delta).toFixed(2))));
      if (next === 1) setOffset(ORIGIN);
      return next;
    });
  }

  function pointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (scale === 1) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      originX: offset.x,
      originY: offset.y,
      moved: false,
    };
  }

  function pointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - drag.x;
    const deltaY = event.clientY - drag.y;
    if (Math.hypot(deltaX, deltaY) > 4) drag.moved = true;
    setOffset({ x: drag.originX + deltaX, y: drag.originY + deltaY });
  }

  function pointerEnd(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    if (dragRef.current.moved) suppressClickRef.current = true;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function closeFromCanvas(event: ReactMouseEvent<HTMLDivElement>) {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    if (event.target === event.currentTarget || event.target instanceof HTMLImageElement) {
      onClose();
    }
  }

  return <dialog
    aria-describedby={description ? descriptionId : undefined}
    aria-label={title}
    className="mermaid-lightbox"
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    ref={dialogRef}
  >
    {description && <p className="mermaid-visually-hidden" id={descriptionId}>{description}</p>}
    <button aria-label="关闭大图" autoFocus className="mermaid-lightbox-close" onClick={onClose} type="button">×</button>
    <div
      className={`mermaid-lightbox-canvas${scale > 1 ? " is-zoomed" : ""}`}
      onClick={closeFromCanvas}
      onPointerCancel={pointerEnd}
      onPointerDown={pointerDown}
      onPointerMove={pointerMove}
      onPointerUp={pointerEnd}
      onWheel={wheel}
    >
      <img
        alt={title}
        className="mermaid-lightbox-image"
        draggable={false}
        src={imageSource}
        style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }}
      />
    </div>
  </dialog>;
}
