/** Abstract artwork for the four steps. Drawn here, not photographs: see ImageSlot for photos. */
type Name = "scan" | "highlight" | "approve" | "verify";

const GOLD = "url(#art-gold)";

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <svg viewBox="0 0 400 300" className="size-full" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id="art-gold" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#f6e3a8" />
          <stop offset="0.55" stopColor="#c9a24b" />
          <stop offset="1" stopColor="#8f6a1c" />
        </linearGradient>
        <radialGradient id="art-glow" cx="0.5" cy="0.45" r="0.6">
          <stop offset="0" stopColor="#c9a24b" stopOpacity="0.35" />
          <stop offset="1" stopColor="#c9a24b" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="400" height="300" fill="#100e0a" />
      <rect width="400" height="300" fill="url(#art-glow)" />
      {children}
    </svg>
  );
}

export function Art({ name }: { name: Name }) {
  if (name === "scan") {
    return (
      <Frame>
        {[40, 70, 100, 130].map((r, i) => (
          <circle
            key={r}
            cx="200"
            cy="150"
            r={r}
            stroke={GOLD}
            strokeWidth="1"
            opacity={0.85 - i * 0.18}
          />
        ))}
        <path d="M200 150 L200 20" stroke={GOLD} strokeWidth="2" strokeLinecap="round">
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="0 200 150"
            to="360 200 150"
            dur="9s"
            repeatCount="indefinite"
          />
        </path>
        <circle cx="200" cy="150" r="5" fill={GOLD} />
        <circle
          cx="262"
          cy="96"
          r="4"
          fill="#f6e3a8"
          className="[animation:gold-pulse_2.4s_ease-in-out_infinite]"
        />
        <circle
          cx="132"
          cy="198"
          r="3"
          fill="#f6e3a8"
          className="[animation:gold-pulse_3.1s_ease-in-out_infinite]"
        />
      </Frame>
    );
  }
  if (name === "highlight") {
    return (
      <Frame>
        <rect x="60" y="50" width="280" height="200" rx="10" stroke="#5a4a22" />
        {[80, 104, 128, 176, 200].map((y) => (
          <rect
            key={y}
            x="84"
            y={y}
            width={y % 48 === 8 ? 150 : 210}
            height="8"
            rx="4"
            fill="#2b2415"
          />
        ))}
        <rect
          x="80"
          y="148"
          width="240"
          height="22"
          rx="6"
          stroke={GOLD}
          strokeWidth="1.5"
          fill="#c9a24b"
          fillOpacity="0.14"
          className="[animation:gold-pulse_3s_ease-in-out_infinite]"
        />
        <circle cx="304" cy="159" r="9" stroke={GOLD} strokeWidth="1.5" />
        <path
          d="M304 154 v6 M304 163 v.5"
          stroke="#f6e3a8"
          strokeWidth="1.8"
          strokeLinecap="round"
        />
      </Frame>
    );
  }
  if (name === "approve") {
    return (
      <Frame>
        <path
          d="M200 40 L290 74 V150 C290 205 250 244 200 262 C150 244 110 205 110 150 V74 Z"
          stroke={GOLD}
          strokeWidth="2"
          fill="#c9a24b"
          fillOpacity="0.08"
        />
        <path
          d="M200 62 L270 88 V150 C270 192 240 224 200 240 C160 224 130 192 130 150 V88 Z"
          stroke="#5a4a22"
        />
        <path
          d="M164 152 L192 180 L242 124"
          stroke={GOLD}
          strokeWidth="7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </Frame>
    );
  }
  return (
    <Frame>
      <rect x="40" y="60" width="150" height="180" rx="10" stroke="#5a4a22" />
      <rect x="210" y="60" width="150" height="180" rx="10" stroke={GOLD} strokeWidth="1.5" />
      {[90, 116, 142].map((y) => (
        <rect key={y} x="58" y={y} width="110" height="8" rx="4" fill="#2b2415" />
      ))}
      <rect x="58" y="168" width="110" height="26" rx="6" stroke="#8a3b2e" strokeDasharray="4 4" />
      {[90, 116, 142, 168].map((y) => (
        <rect key={y} x="228" y={y} width="110" height="8" rx="4" fill="#3a3019" />
      ))}
      <circle cx="300" cy="212" r="16" stroke={GOLD} strokeWidth="2" />
      <path
        d="M292 212 l6 6 l12 -13"
        stroke="#f6e3a8"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Frame>
  );
}
