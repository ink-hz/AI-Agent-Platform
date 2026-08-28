import { useEffect, useId, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";


type MermaidLightboxProps = {
  imageSource: string;
  title: string;
  description: string | null;
  onClose: () => void;
};

type Point = { x: number; y: number };
type DragState = Point & { pointerId: number; originX: number; originY: number };

const ORIGIN: Point = { x: 0, y: 0 };


export function MermaidLightbox({ imageSource, title, description, onClose }: MermaidLightboxProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const dragRef = useRef<DragState | null>(null);
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

  function changeScale(delta: number) {
    const next = Math.min(4, Math.max(1, Number((scale + delta).toFixed(2))));
    setScale(next);
    if (next === 1) setOffset(ORIGIN);
  }

  function resetView() {
    setScale(1);
    setOffset(ORIGIN);
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
    };
  }

  function pointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setOffset({
      x: drag.originX + event.clientX - drag.x,
      y: drag.originY + event.clientY - drag.y,
    });
  }

  function pointerEnd(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
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
    <div className="mermaid-lightbox-toolbar">
      <output aria-live="polite">{Math.round(scale * 100)}%</output>
      <button aria-label="缩小" disabled={scale === 1} onClick={() => changeScale(-.25)} type="button">−</button>
      <button aria-label="放大" disabled={scale === 4} onClick={() => changeScale(.25)} type="button">＋</button>
      <button onClick={resetView} type="button">恢复</button>
      <button aria-label="关闭大图" autoFocus onClick={onClose} type="button">×</button>
    </div>
    <div
      className={`mermaid-lightbox-canvas${scale > 1 ? " is-zoomed" : ""}`}
      onPointerCancel={pointerEnd}
      onPointerDown={pointerDown}
      onPointerMove={pointerMove}
      onPointerUp={pointerEnd}
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
