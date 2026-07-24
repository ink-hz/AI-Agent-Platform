import { formatCount } from "./fleet";
import { UI_COPY } from "./copy";
import type { TrendPoint } from "./types";


interface UsageTrendProps {
  trend: TrendPoint[];
}


function shortDate(value: string): string {
  const match = /\d{4}-(\d{2})-(\d{2})/.exec(value);
  return match ? `${match[1]}/${match[2]}` : value;
}


export function UsageTrend({ trend }: UsageTrendProps) {
  const maximum = Math.max(0, ...trend.map((point) => point.conversations));
  const total = trend.reduce((sum, point) => sum + point.conversations, 0);

  return (
    <article className="insight-card trend-card">
      <div className="insight-heading">
        <div><h2>{UI_COPY.insights.trend}</h2></div>
        <span>{UI_COPY.insights.conversations(formatCount(total))}</span>
      </div>

      <div className={`trend-chart ${maximum === 0 ? "is-empty" : ""}`}>
        {trend.map((point) => {
          const ratio = maximum === 0 ? 0 : point.conversations / maximum;
          return (
            <div
              className="trend-column"
              key={point.date}
              aria-label={`${point.date}，${UI_COPY.insights.conversations(String(point.conversations))}`}
            >
              <span className="trend-value">{formatCount(point.conversations)}</span>
              <span className="trend-track" aria-hidden="true">
                <i style={{ height: `${Math.max(ratio * 100, point.conversations > 0 ? 8 : 0)}%` }} />
              </span>
              <span className="trend-date">{shortDate(point.date)}</span>
            </div>
          );
        })}
        {maximum === 0 && (
          <p className="trend-empty">{UI_COPY.insights.emptyTrend}</p>
        )}
      </div>
    </article>
  );
}
