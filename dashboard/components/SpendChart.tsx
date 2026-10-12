import { inr, inrShort } from "@/lib/format";
import type { DailySeries } from "@/lib/overview-extras";

const W = 640;
const H = 200;
const PAD = { top: 18, right: 12, bottom: 26, left: 12 };

export function SpendChart({ series, thisLabel, lastLabel }: { series: DailySeries; thisLabel: string; lastLabel: string }) {
  const max = Math.max(1, ...series.points.map((p) => Math.max(p.thisCum ?? 0, p.lastCum)));
  const x = (day: number) => PAD.left + ((day - 1) / Math.max(1, series.daysInMonth - 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - v / max) * (H - PAD.top - PAD.bottom);
  const last = series.points.map((p) => `${x(p.day).toFixed(1)},${y(p.lastCum).toFixed(1)}`).join(" ");
  const thisPts = series.points.filter((p) => p.thisCum !== null);
  const now = thisPts.at(-1);
  const area = now
    ? `M${x(1)},${y(0)} ` + thisPts.map((p) => `L${x(p.day).toFixed(1)},${y(p.thisCum ?? 0).toFixed(1)}`).join(" ") + ` L${x(now.day)},${y(0)} Z`
    : "";
  const line = thisPts.map((p) => `${x(p.day).toFixed(1)},${y(p.thisCum ?? 0).toFixed(1)}`).join(" ");
  const gridValues = [0.25, 0.5, 0.75, 1].map((f) => f * max);

  return (
    <figure className="spend-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Cumulative spend by day: ${thisLabel} ${inr(now?.thisCum ?? 0)} by day ${series.today}; ${lastLabel} ${inr(series.lastAtToday)} at the same day.`}>
        <defs>
          <linearGradient id="spendFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#f4f2ed" stopOpacity="0.16" />
            <stop offset="1" stopColor="#f4f2ed" stopOpacity="0" />
          </linearGradient>
        </defs>
        {gridValues.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} className="grid" />
            <text x={W - PAD.right} y={y(v) - 5} textAnchor="end" className="tick">{inrShort(v)}</text>
          </g>
        ))}
        <polyline points={last} className="last" />
                {line ? <polyline points={line} className="this" /> : null}
        {now ? (
          <g>
            <line x1={x(now.day)} x2={x(now.day)} y1={PAD.top - 6} y2={H - PAD.bottom} className="today" />
            <circle cx={x(now.day)} cy={y(now.thisCum ?? 0)} r="5" className="now" />
          </g>
        ) : null}
        <text x={PAD.left} y={H - 6} className="tick">1</text>
        {now ? <text x={x(now.day)} y={H - 6} textAnchor="middle" className="tick strong">today · {now.day}</text> : null}
        <text x={W - PAD.right} y={H - 6} textAnchor="end" className="tick">{series.daysInMonth}</text>
      </svg>
      <figcaption className="chart-key">
        <span><i className="k-this" />{thisLabel}</span>
        <span><i className="k-last" />{lastLabel}</span>
      </figcaption>
    </figure>
  );
}
