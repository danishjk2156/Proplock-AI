import { useRef, useState } from "react";
import { Icon } from "./Icon";

export interface Slide { src: string; alt: string; caption: string }

/**
 * The owner's photos (never the broker's), whole and uncropped on a matte band.
 * Arrows and a counter, thumbnails on wide screens, swipe on phones, arrow keys when focused.
 */
export function Carousel({ slides, label }: { slides: Slide[]; label: string }) {
  const [i, setI] = useState(0);
  const start = useRef<number | null>(null);
  const n = slides.length;
  const go = (to: number) => setI(((to % n) + n) % n);

  if (n === 0) {
    return (
      <div className="carousel empty">
        <div className="carousel-frame ph"><span className="ph-label">No owner photos yet</span></div>
      </div>
    );
  }
  const s = slides[i];
  return (
    <section className="carousel" aria-roledescription="carousel" aria-label={label}
      onKeyDown={(e) => { if (e.key === "ArrowRight") go(i + 1); if (e.key === "ArrowLeft") go(i - 1); }}>
      <div className="carousel-frame"
        onPointerDown={(e) => { if (e.pointerType !== "mouse") start.current = e.clientX; }}
        onPointerUp={(e) => {
          if (start.current === null) return;
          const dx = e.clientX - start.current;
          start.current = null;
          if (Math.abs(dx) > 40) go(dx < 0 ? i + 1 : i - 1);
        }}>
        <img src={s.src} alt={s.alt} draggable={false} />
        {n > 1 && (
          <>
            <button className="carousel-btn prev" aria-label="Previous photo" onClick={() => go(i - 1)}><Icon name="back" size={20} /></button>
            <button className="carousel-btn next" aria-label="Next photo" onClick={() => go(i + 1)}><Icon name="arrow" size={20} /></button>
          </>
        )}
        <span className="carousel-count" aria-live="polite">{s.caption} · {i + 1} / {n}</span>
      </div>
      {n > 1 && (
        <div className="carousel-thumbs">
          {slides.map((t, k) => (
            <button key={t.src} className="thumb-btn" aria-label={`Show photo ${k + 1}: ${t.caption}`} aria-current={k === i} onClick={() => go(k)}>
              <img src={t.src} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
