import { formatNumber } from "@/lib/format";
import type { SentimentCounts, ThemeTrend, TrendInterval, TrendPoint } from "@/lib/types";

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

/**
 * What a bar's height shows:
 * - count: the theme's reviews in the period.
 * - share: the theme's reviews as a share of all the entity's reviews in the period, so a busy
 *   period doesn't look like a surge.
 * - mix: the sentiment of the theme's reviews in the period, each bar adding up to 100%.
 */
export type TrendMode = "count" | "share" | "mix";

// A share needs this many reviews behind it before it means much; smaller bars are hatched.
export const MIN_PERIOD_REVIEWS = 20; // the entity's reviews in the period, for the share
export const MIN_THEME_REVIEWS = 10; // the theme's reviews in the period, for the sentiment mix

// Diagonal stripes over a bar's colours mark a share computed from too few reviews.
const HATCH = "repeating-linear-gradient(135deg, var(--surface) 0 2px, transparent 2px 6px)";

export function periodName(periodStart: string, interval: TrendInterval): string {
  const year = periodStart.slice(0, 4);
  const month = Number(periodStart.slice(5, 7)) - 1;
  if (interval === "year") return year;
  if (interval === "quarter") return `${year} Q${Math.floor(month / 3) + 1}`;
  return `${MONTHS[month]} ${year}`;
}

const percent = (value: number) => {
  const rounded = Math.round(value * 100);
  return value > 0 && rounded === 0 ? "<1%" : `${rounded}%`;
};

/** The denominator a bar is divided by in each mode (1 for counts). */
function denominator(point: TrendPoint, mode: TrendMode): number {
  if (mode === "share") return point.period_reviews;
  if (mode === "mix") return point.sentiment.total;
  return 1;
}

/** Whether a bar's share rests on too few reviews to read much into. */
export function isSmallSample(point: TrendPoint, mode: TrendMode): boolean {
  if (mode === "share") return point.period_reviews < MIN_PERIOD_REVIEWS;
  if (mode === "mix") return point.sentiment.total > 0 && point.sentiment.total < MIN_THEME_REVIEWS;
  return false;
}

function describe(point: TrendPoint, interval: TrendInterval, mode: TrendMode): string {
  const { positive, negative, mixed, neutral, total } = point.sentiment;
  const name = periodName(point.period_start, interval);
  if (mode === "count") {
    return `${name}: ${total} reviews (${positive} positive, ${mixed} mixed, ${neutral} neutral, ${negative} negative)`;
  }
  const caution = isSmallSample(point, mode) ? " Too few reviews to read much into." : "";
  if (mode === "share") {
    return `${name}: ${total} of ${point.period_reviews} reviews (${percent(total / point.period_reviews)}) raise this theme.${caution}`;
  }
  if (total === 0) return `${name}: no reviews raise this theme.`;
  const part = (value: number) => percent(value / total);
  return `${name}: of ${total} reviews, ${part(positive)} positive, ${part(mixed)} mixed, ${part(neutral)} neutral, ${part(negative)} negative.${caution}`;
}

/**
 * The theme's reviews per period, stacked by sentiment, as counts or shares (see TrendMode). Every
 * period in which the entity has reviews has a slot. Each bar is a toggle: the chosen periods (their
 * period_start) filter the reviews listed below.
 */
export function TrendChart({
  trend,
  interval,
  mode,
  selected,
  onToggle,
}: {
  trend: ThemeTrend;
  interval: TrendInterval;
  mode: TrendMode;
  selected: string[];
  onToggle: (periodStart: string) => void;
}) {
  if (!trend.points.some((point) => point.sentiment.total > 0)) {
    return <p className="text-muted">None of this theme&apos;s reviews has a date.</p>;
  }
  const value = (point: TrendPoint, key: keyof SentimentCounts) => {
    const whole = denominator(point, mode);
    return whole ? point.sentiment[key] / whole : 0;
  };
  // A tiny period (1 of 2 reviews is 50%) would flatten every other bar, so the scale comes from
  // the periods with enough reviews when there are any. A small period's bar that is taller still
  // is shrunk whole to the top of the chart (it is striped, and its tooltip has the real numbers).
  const reliable = trend.points.filter((point) => !isSmallSample(point, mode));
  const scaled = reliable.length > 0 ? reliable : trend.points;
  const max = mode === "mix" ? 1 : Math.max(...scaled.map((point) => value(point, "total")));
  const height = (point: TrendPoint, key: keyof SentimentCounts) => {
    const amount = value(point, key);
    if (!amount) return 0;
    const fit = Math.min(1, max / value(point, "total"));
    return Math.max(1, Math.round((amount * fit * CHART_HEIGHT) / max));
  };
  const axisTop = mode === "count" ? formatNumber(max) : percent(max);
  const hatched = trend.points.some((point) => isSmallSample(point, mode));

  let previousYear = "";
  const bars = trend.points.map((point) => {
    const year = point.period_start.slice(0, 4);
    const label = year !== previousYear ? year : "";
    previousYear = year;
    return { key: point.period_start, label, point, tip: describe(point, interval, mode) };
  });
  const barStyle = { flex: `1 0 ${MIN_BAR_WIDTH[interval]}`, maxWidth: "56px" };
  const what = { count: "Reviews", share: "Share of reviews", mix: "Sentiment mix of reviews" }[mode];

  return (
    <div className="flex flex-col gap-2">
      <div
        role="group"
        aria-label={`${what} per ${interval}, from ${periodName(trend.points[0].period_start, interval)} to ${periodName(trend.points.at(-1)!.period_start, interval)}, up to ${axisTop}. Select bars to list their reviews.`}
        className="flex gap-2.5"
      >
        <div className="flex h-[200px] min-w-8 flex-col justify-between text-right text-xs text-muted">
          <span>{axisTop}</span>
          <span>0</span>
        </div>
        {/* Right-to-left scrolling opens a long history at its latest period; the content stays left-to-right. */}
        <div className="min-w-0 flex-1 overflow-x-auto" style={{ direction: "rtl" }}>
          <div style={{ direction: "ltr", width: "max-content", minWidth: "100%" }}>
            <div className="flex h-[200px] items-end gap-[3px] border-b border-line-strong pt-2">
              {bars.map((bar) => {
                const isSelected = selected.includes(bar.key);
                const dimmed = selected.length > 0 && !isSelected;
                const empty = bar.point.sentiment.total === 0;
                const small = isSmallSample(bar.point, mode);
                return (
                  <button
                    key={bar.key}
                    type="button"
                    title={bar.tip}
                    aria-label={bar.tip}
                    aria-pressed={isSelected}
                    disabled={empty}
                    onClick={() => onToggle(bar.key)}
                    className={`flex h-full flex-col justify-end overflow-hidden rounded-t-sm enabled:cursor-pointer enabled:hover:bg-subtle ${
                      dimmed ? "opacity-35 hover:opacity-70" : ""
                    }`}
                    style={barStyle}
                  >
                    {STACK.map(({ key, className }) => (
                      <span
                        key={key}
                        className={`block shrink-0 ${className}`}
                        style={{
                          height: height(bar.point, key),
                          backgroundImage: small ? HATCH : undefined,
                        }}
                      />
                    ))}
                  </button>
                );
              })}
            </div>
            <div className="flex h-6 gap-[3px]" aria-hidden="true">
              {bars.map((bar) => (
                <div
                  key={bar.key}
                  className={`overflow-visible border-t-[3px] text-xs whitespace-nowrap text-muted ${
                    selected.includes(bar.key) ? "border-fg" : "border-transparent"
                  }`}
                  style={barStyle}
                >
                  {bar.label}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      {hatched && (
        <p className="flex items-center gap-2 text-[13px] text-muted">
          <span aria-hidden="true" className="inline-block h-3 w-3 bg-neutral" style={{ backgroundImage: HATCH }} />
          {mode === "share"
            ? `Striped: under ${MIN_PERIOD_REVIEWS} reviews that ${interval}, too few to read much into.`
            : `Striped: under ${MIN_THEME_REVIEWS} reviews of this theme that ${interval}, too few to read much into.`}
        </p>
      )}
    </div>
  );
}
