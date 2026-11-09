import { formatNumber } from "@/lib/format";
import type { ThemeTrend, TrendInterval } from "@/lib/types";

const CHART_HEIGHT = 188;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Months and quarters keep a readable width; a long history scrolls sideways instead of shrinking.
const MIN_BAR_WIDTH: Record<TrendInterval, string> = { month: "16px", quarter: "16px", year: "0px" };
// Top to bottom, so positive sits on the baseline.
const STACK = [
  { key: "negative", className: "bg-neg" },
  { key: "neutral", className: "bg-neutral" },
  { key: "mixed", className: "bg-mixed" },
  { key: "positive", className: "bg-pos" },
] as const;

function periodName(periodStart: string, interval: TrendInterval): string {
  const year = periodStart.slice(0, 4);
  const month = Number(periodStart.slice(5, 7)) - 1;
  if (interval === "year") return year;
  if (interval === "quarter") return `${year} Q${Math.floor(month / 3) + 1}`;
  return `${MONTHS[month]} ${year}`;
}

/** Reviews per period, stacked by sentiment. Periods with no reviews are left out by the API. */
export function TrendChart({ trend, interval }: { trend: ThemeTrend; interval: TrendInterval }) {
  if (trend.points.length === 0) {
    return <p className="text-muted">None of this theme&apos;s reviews has a date.</p>;
  }
  const max = Math.max(...trend.points.map((point) => point.sentiment.total));
  const height = (value: number) => (value ? Math.max(1, Math.round((value / max) * CHART_HEIGHT)) : 0);

  let previousYear = "";
  const bars = trend.points.map((point) => {
    const year = point.period_start.slice(0, 4);
    const label = year !== previousYear ? year : "";
    previousYear = year;
    const { positive, negative, mixed, neutral, total } = point.sentiment;
    return {
      key: point.period_start,
      label,
      counts: point.sentiment,
      tip: `${periodName(point.period_start, interval)}: ${total} reviews (${positive} positive, ${mixed} mixed, ${neutral} neutral, ${negative} negative)`,
    };
  });
  const barStyle = { flex: `1 0 ${MIN_BAR_WIDTH[interval]}`, maxWidth: "56px" };

  return (
    <div
      role="img"
      aria-label={`Reviews per ${interval} by sentiment, from ${periodName(trend.points[0].period_start, interval)} to ${periodName(trend.points.at(-1)!.period_start, interval)}, peaking at ${formatNumber(max)}`}
      className="flex gap-2.5"
    >
      <div className="flex h-[200px] min-w-8 flex-col justify-between text-right text-xs text-muted">
        <span>{formatNumber(max)}</span>
        <span>0</span>
      </div>
      {/* Right-to-left scrolling opens a long history at its latest period; the content stays left-to-right. */}
      <div className="min-w-0 flex-1 overflow-x-auto" style={{ direction: "rtl" }}>
        <div style={{ direction: "ltr", width: "max-content", minWidth: "100%" }}>
          <div className="flex h-[200px] items-end gap-[3px] border-b border-line-strong pt-2">
            {bars.map((bar) => (
              <div key={bar.key} title={bar.tip} className="flex h-full flex-col justify-end" style={barStyle}>
                {STACK.map(({ key, className }) => (
                  <span key={key} className={`block ${className}`} style={{ height: height(bar.counts[key]) }} />
                ))}
              </div>
            ))}
          </div>
          <div className="flex h-5 gap-[3px] pt-1" aria-hidden="true">
            {bars.map((bar) => (
              <div key={bar.key} className="overflow-visible text-xs whitespace-nowrap text-muted" style={barStyle}>
                {bar.label}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
