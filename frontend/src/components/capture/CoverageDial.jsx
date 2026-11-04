import { MAX_PHOTOS, MIN_PHOTOS } from "../../lib/config";

/** A ring of 15 segments: one fills per photo. Segments 8-12 are the sweet spot for a room. */
export default function CoverageDial({ count }) {
  const R = 52;
  const C = 2 * Math.PI * R;
  const gap = 4;
  const seg = C / MAX_PHOTOS;
  const ready = count >= MIN_PHOTOS;
  return (
    <div className="dial" role="img" aria-label={`${count} of ${MAX_PHOTOS} photos taken`}>
      <svg viewBox="0 0 128 128" width="128" height="128">
        {Array.from({ length: MAX_PHOTOS }, (_, i) => (
          <circle
            key={i}
            cx="64"
            cy="64"
            r={R}
            fill="none"
            strokeWidth="9"
            strokeLinecap="butt"
            transform="rotate(-90 64 64)"
            strokeDasharray={`${seg - gap} ${C - seg + gap}`}
            strokeDashoffset={-i * seg}
            className={i < count ? (i >= 7 && i < 12 ? "seg-on seg-best" : "seg-on") : "seg-off"}
          />
        ))}
      </svg>
      <div className="dial__center">
        <strong>{count}</strong>
        <span>{ready ? (count < 8 ? "enough, more is better" : count <= 12 ? "great coverage" : "plenty") : `${MIN_PHOTOS - count} more needed`}</span>
      </div>
    </div>
  );
}
