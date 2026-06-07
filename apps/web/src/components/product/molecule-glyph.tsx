import { clsx } from "clsx";

export function MoleculeGlyph({
  label,
  className,
  dense = false,
}: {
  label: string;
  className?: string;
  dense?: boolean;
}) {
  return (
    <div
      className={clsx(
        "relative overflow-hidden rounded-product border border-slatewash-200 bg-slatewash-50 molecule-grid",
        dense ? "h-24" : "h-36",
        className,
      )}
    >
      <svg
        aria-label={`${label} molecule sketch`}
        viewBox="0 0 220 130"
        className="absolute inset-0 h-full w-full text-ink-800"
        role="img"
      >
        <g fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3">
          <path d="M34 72 L65 45 L101 56 L128 31 L166 45 L188 77 L158 101 L119 92 L85 111 L52 96 Z" />
          <path d="M65 45 L85 111" opacity="0.7" />
          <path d="M101 56 L119 92" opacity="0.7" />
          <path d="M128 31 L158 101" opacity="0.7" />
        </g>
        <g fill="currentColor">
          <circle cx="65" cy="45" r="5" />
          <circle cx="128" cy="31" r="5" />
          <circle cx="188" cy="77" r="5" />
          <circle cx="85" cy="111" r="5" />
        </g>
        <g fill="#159a9c">
          <circle cx="101" cy="56" r="6" />
          <circle cx="158" cy="101" r="6" />
        </g>
      </svg>
      <span className="absolute bottom-2 left-2 rounded-product border border-slatewash-200 bg-white/90 px-2 py-1 text-xs font-semibold text-ink-800">
        {label}
      </span>
    </div>
  );
}
