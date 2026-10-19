/** Fine golden lines that draw themselves once. Pure CSS animation, so it works without scripts. */
export function GoldLines({ className = "" }: { className?: string }) {
  const paths = [
    "M-40 520 C 220 380, 380 640, 640 470 S 1060 260, 1260 340",
    "M-40 600 C 260 470, 420 700, 700 540 S 1100 330, 1260 420",
    "M-40 440 C 200 300, 360 560, 600 400 S 1040 190, 1260 260",
    "M200 -20 C 260 160, 120 300, 260 460",
  ];
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 1200 700"
      preserveAspectRatio="xMidYMid slice"
      className={className}
      fill="none"
    >
      <defs>
        <linearGradient id="gl-grad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#8f6a1c" stopOpacity="0" />
          <stop offset="0.35" stopColor="#dcb967" />
          <stop offset="0.7" stopColor="#f6e3a8" />
          <stop offset="1" stopColor="#c9a24b" stopOpacity="0.2" />
        </linearGradient>
        <filter id="gl-glow" x="-10%" y="-10%" width="120%" height="120%">
          <feGaussianBlur stdDeviation="3.5" />
        </filter>
      </defs>
      {paths.map((d, i) => (
        <g key={d}>
          <path
            d={d}
            pathLength={1}
            stroke="url(#gl-grad)"
            strokeWidth={5}
            filter="url(#gl-glow)"
            className="gold-line"
            style={{ animationDelay: `${i * 0.45}s`, opacity: 0.55 }}
          />
          <path
            d={d}
            pathLength={1}
            stroke="url(#gl-grad)"
            strokeWidth={1.4}
            className="gold-line"
            style={{ animationDelay: `${i * 0.45}s` }}
          />
        </g>
      ))}
    </svg>
  );
}
