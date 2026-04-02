/**
 * Reusable loading skeleton components for the quant dashboard.
 * Use these to show loading states while data is being fetched.
 */

export function SkeletonCard({ className = "" }: { className?: string }) {
  return (
    <div className={`card-q p-4 animate-pulse ${className}`}>
      <div className="h-3 rounded mb-3" style={{ backgroundColor: "#2b3139", width: "40%" }} />
      <div className="h-7 rounded mb-2" style={{ backgroundColor: "#2b3139", width: "60%" }} />
      <div className="h-3 rounded" style={{ backgroundColor: "#1e2329", width: "50%" }} />
    </div>
  );
}

export function SkeletonRow({ cols = 5 }: { cols?: number }) {
  return (
    <tr style={{ borderBottom: "1px solid #1e2329" }}>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-4 rounded animate-pulse" style={{ backgroundColor: "#1e2329", width: i === 0 ? "80%" : i === 1 ? "60%" : "40%" }} />
        </td>
      ))}
    </tr>
  );
}

export function SkeletonChart({ height = 160 }: { height?: number }) {
  return (
    <div className="animate-pulse rounded-lg" style={{ height, backgroundColor: "#1e2329" }}>
      <div className="flex items-end justify-around h-full px-4 pb-4 pt-8">
        {Array.from({ length: 7 }).map((_, i) => (
          <div
            key={i}
            className="rounded-t"
            style={{
              backgroundColor: "#2b3139",
              width: 24,
              height: `${30 + Math.random() * 60}%`,
            }}
          />
        ))}
      </div>
    </div>
  );
}

export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2 animate-pulse">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-3 rounded"
          style={{ backgroundColor: "#2b3139", width: i === lines - 1 ? "60%" : "100%" }}
        />
      ))}
    </div>
  );
}
