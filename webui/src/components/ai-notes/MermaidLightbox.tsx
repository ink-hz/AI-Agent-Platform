import { useEffect, useId, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
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
  const instructionsId = useId();
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

  function changeScale(delta: number) {
    setScale((current) => {
      const next = Math.min(4, Math.max(1, Number((current + delta).toFixed(2))));
      if (next === 1) setOffset(ORIGIN);
      return next;
    });
  }

  function wheel(event: ReactWheelEvent<HTMLDivElement>) {
    if (event.deltaY === 0) return;
    event.preventDefault();
    changeScale(event.deltaY < 0 ? .25 : -.25);
  }

  function keyDown(event: ReactKeyboardEvent<HTMLDialogElement>) {
    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      changeScale(.25);
      return;
    }
    if (event.key === "-" || event.key === "_") {
      event.preventDefault();
      changeScale(-.25);
      return;
    }
    if (event.key === "0") {
      event.preventDefault();
      setScale(1);
      setOffset(ORIGIN);
      return;
    }
    if (scale === 1 || !event.key.startsWith("Arrow")) return;
    const movement: Record<string, Point> = {
      ArrowUp: { x: 0, y: 32 },
      ArrowDown: { x: 0, y: -32 },
      ArrowLeft: { x: 32, y: 0 },
      ArrowRight: { x: -32, y: 0 },
    };
    const delta = movement[event.key];
    if (!delta) return;
    event.preventDefault();
    setOffset((current) => ({ x: current.x + delta.x, y: current.y + delta.y }));
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

  function finishPointer(event: ReactPointerEvent<HTMLDivElement>, suppressClick: boolean) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    if (suppressClick && dragRef.current.moved) suppressClickRef.current = true;
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
    aria-describedby={`${description ? `${descriptionId} ` : ""}${instructionsId}`}
    aria-label={title}
    className="mermaid-lightbox"
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onKeyDown={keyDown}
    ref={dialogRef}
  >
    {description && <p className="mermaid-visually-hidden" id={descriptionId}>{description}</p>}
    <p className="mermaid-visually-hidden" id={instructionsId}>使用加减键缩放，方向键移动，数字 0 恢复；点击图片或按 Esc 退出。</p>
    <button aria-label="关闭大图" autoFocus className="mermaid-lightbox-close" onClick={onClose} type="button">×</button>
    <div
      className={`mermaid-lightbox-canvas${scale > 1 ? " is-zoomed" : ""}`}
      onClick={closeFromCanvas}
      onPointerCancel={(event) => finishPointer(event, false)}
      onPointerDown={pointerDown}
      onPointerMove={pointerMove}
      onPointerUp={(event) => finishPointer(event, true)}
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
