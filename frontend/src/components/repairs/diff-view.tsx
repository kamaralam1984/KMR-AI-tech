/** A unified diff, colored by line type and also marked with +/- so color is never the only cue. */
export function DiffView({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  return (
    <pre
      aria-label="Proposed change"
      tabIndex={0}
      className="max-h-96 overflow-auto rounded-md border border-line bg-page p-3 text-xs leading-5"
    >
      {lines.map((line, index) => {
        const tone =
          line.startsWith("+") && !line.startsWith("+++")
            ? "bg-success-bg text-success-text"
            : line.startsWith("-") && !line.startsWith("---")
              ? "bg-danger-bg text-danger-text"
              : line.startsWith("@@")
                ? "text-info-text"
                : "text-muted";
        return (
          <span key={index} className={`block px-1 whitespace-pre ${tone}`}>
            {line || " "}
          </span>
        );
      })}
    </pre>
  );
}
