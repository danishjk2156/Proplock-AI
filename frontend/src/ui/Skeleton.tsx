// Loading placeholders that keep the page's shape (S19). They are hidden from screen readers;
// one live "Loading…" message stands in for them.

const HEIGHTS = ["4 / 5", "1 / 1", "3 / 4", "16 / 11", "4 / 5", "1 / 1", "3 / 4", "16 / 11"];

export function SkeletonPins({ count = 6, label = "Loading…" }: { count?: number; label?: string }) {
  return (
    <div>
      <p className="visually-hidden" role="status">{label}</p>
      <div className="masonry" aria-hidden="true">
        {Array.from({ length: count }, (_, i) => (
          <div key={i} className="pin-item">
            <div className="pin skel-pin">
              <div className="pin-media skel" style={{ aspectRatio: HEIGHTS[i % HEIGHTS.length] }} />
              <span className="pin-cap">
                <span className="skel skel-line" style={{ width: "70%" }} />
                <span className="skel skel-line" style={{ width: "40%" }} />
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function SkeletonLines({ lines = 3, label = "Loading…" }: { lines?: number; label?: string }) {
  return (
    <div className="stack" style={{ gap: 8 }}>
      <p className="visually-hidden" role="status">{label}</p>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} aria-hidden="true" className="skel skel-line" style={{ width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}
