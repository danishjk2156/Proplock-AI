import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";

export interface PinImage {
  src: string;
  alt: string;
  /** Real pixel size, so the browser reserves the space and pins keep their own proportions. */
  width?: number;
  height?: number;
}

/**
 * Masonry columns: CSS only, so there is no layout shift and DOM order is reading order.
 * Two columns on phones, then as many 236 px columns as fit, at most five.
 */
export function Masonry({ children, label, className = "" }: { children: ReactNode; label?: string; className?: string }) {
  return <div className={`masonry ${className}`} role={label ? "list" : undefined} aria-label={label}>{children}</div>;
}

/**
 * An image-first card. The whole pin is one link (or button) named by its title; overlays and
 * the caption are inside it. Without a photo it shows a placeholder that looks like one.
 */
export function Pin({ to, onClick, image, ratio = "4 / 5", badges, tag, title, meta, children, dashed, listItem }: {
  to?: string;
  onClick?: () => void;
  image?: PinImage | null;
  /** Placeholder shape when there is no image (CSS aspect-ratio). */
  ratio?: string;
  /** Top-left overlay: status pills. */
  badges?: ReactNode;
  /** Bottom-left overlay on the photo: price or count. */
  tag?: ReactNode;
  title: string;
  meta?: ReactNode;
  children?: ReactNode;
  /** A dashed "start something" pin (no photo, an invitation). */
  dashed?: boolean;
  listItem?: boolean;
}) {
  const style: CSSProperties | undefined =
    image?.width && image?.height ? undefined : { aspectRatio: ratio };
  const media = (
    <div className={`pin-media${image ? "" : " ph"}`} style={style}>
      {image ? (
        <img src={image.src} alt={image.alt} width={image.width} height={image.height} loading="lazy" decoding="async" />
      ) : (
        <span className="ph-label" aria-hidden="true">{dashed ? "+" : "No photo yet"}</span>
      )}
      {(badges || tag) && <span className="pin-fade" aria-hidden="true" />}
      {badges && <span className="pin-badges">{badges}</span>}
      {tag && <span className="pin-tag">{tag}</span>}
    </div>
  );
  const body = (
    <>
      {media}
      <span className="pin-cap">
        <span className="pin-title">{title}</span>
        {meta && <span className="pin-meta">{meta}</span>}
        {children}
      </span>
    </>
  );
  const cls = `pin${dashed ? " dashed" : ""}`;
  const inner = to ? (
    <Link to={to} className={cls}>{body}</Link>
  ) : onClick ? (
    <button type="button" className={cls} onClick={onClick}>{body}</button>
  ) : (
    <div className={cls}>{body}</div>
  );
  return listItem ? <div role="listitem" className="pin-item">{inner}</div> : <div className="pin-item">{inner}</div>;
}

/** A board: title, count and a 2×2 collage cover of up to four photos (placeholders fill the rest). */
export function Board({ to, title, count, covers, note }: {
  to?: string;
  title: string;
  count: number;
  covers: (PinImage | null)[];
  note?: string;
}) {
  const four = [...covers.slice(0, 4), null, null, null, null].slice(0, 4);
  const body = (
    <>
      <span className="board-cover" aria-hidden="true">
        {four.map((c, i) => (c ? <img key={i} src={c.src} alt="" loading="lazy" /> : <span key={i} className="ph" />))}
      </span>
      <span className="board-title">{title}</span>
      <span className="board-meta">{count} {count === 1 ? "listing" : "listings"}{note ? ` · ${note}` : ""}</span>
    </>
  );
  return to ? <Link to={to} className="board">{body}</Link> : <div className="board">{body}</div>;
}
