/** Tiny twinkling stars at fixed pseudo-random places (the same on server and client). */
export function Sparkles({ count = 22, className = "" }: { count?: number; className?: string }) {
  const unit = (n: number) => {
    const x = Math.sin(n * 12.9898) * 43758.5453;
    return x - Math.floor(x);
  };
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`}
    >
      {Array.from({ length: count }, (_, i) => (
        <span
          key={i}
          className="sparkle"
          style={{
            left: `${(unit(i + 1) * 100).toFixed(2)}%`,
            top: `${(unit(i + 101) * 100).toFixed(2)}%`,
            width: `${8 + Math.round(unit(i + 201) * 14)}px`,
            height: `${8 + Math.round(unit(i + 201) * 14)}px`,
            ["--dur" as string]: `${(3 + unit(i + 301) * 4).toFixed(2)}s`,
            ["--delay" as string]: `${(unit(i + 401) * 6).toFixed(2)}s`,
          }}
        />
      ))}
    </div>
  );
}
