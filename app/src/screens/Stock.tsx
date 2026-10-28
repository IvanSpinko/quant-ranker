// One stock: growth vs the market, rank history, today's features and what drove the score.
import { useEffect, useMemo, useState } from "react";

import { api, type Overview, type StockDetail } from "../api";
import { Chart } from "../components/Chart";
import { Bars, Card, ErrorBox, Explain, Metric } from "../components/ui";
import { dayToSeconds, featureLabel, modelLabel, num, pct } from "../format";

interface Props {
  overview: Overview;
  ticker: string | null;
  openStock: (ticker: string) => void;
}

export function StockScreen({ overview, ticker, openStock }: Props) {
  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!ticker) return;
    setError(null);
    api.stock(ticker).then(setDetail, (e) => {
      setDetail(null);
      setError(String(e));
    });
  }, [ticker]);

  const price = useMemo(() => {
    if (!detail) return { x: [], lines: [] };
    return {
      x: detail.price.t.map(dayToSeconds),
      lines: [
        { label: detail.ticker, values: detail.price.v, color: "--c-net" },
        { label: "Market (SPY)", values: detail.market.v, color: "--c-market", dash: true },
      ],
    };
  }, [detail]);
  const ranks = useMemo(() => {
    if (!detail) return { x: [], lines: [] };
    // Weekly points keep the chart readable; ranks change daily but the strategy trades weekly.
    const keep = detail.score_rank.t.map((_, i) => i % 5 === 0);
    return {
      x: detail.score_rank.t.filter((_, i) => keep[i]).map(dayToSeconds),
      lines: [{ label: "Ranking percentile", values: detail.score_rank.v.filter((_, i) => keep[i]), color: "--c-accent", fill: true }],
    };
  }, [detail]);

  const search = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) openStock(query.trim().toUpperCase());
  };

  return (
    <div className="screen">
      <form className="toolbar" onSubmit={search}>
        <input className="input" placeholder="Ticker, e.g. AAPL" value={query} onChange={(e) => setQuery(e.target.value)} />
        <button className="btn" type="submit">
          Open
        </button>
      </form>
      {!ticker && <Explain>Pick a stock in the ranking on the Signals screen or type a ticker.</Explain>}
      {error && <ErrorBox error={error} />}
      {detail && (
        <>
          <div className="stock-head">
            <div>
              <h1>
                {detail.ticker} <span className="muted">{detail.name}</span>
              </h1>
              <div className="muted">
                {detail.sector} · {detail.industry}
              </div>
            </div>
            {detail.signal && (
              <div className="metrics">
                <Metric label="Rank today" value={`${detail.signal.rank} of ${overview.n_tickers}`} />
                <Metric label="Percentile" value={pct(detail.signal.pct, 0)} hint="100% is the best score, 0% the worst" />
              </div>
            )}
          </div>

          <Card title="Growth of $100">
            <Explain>What $100 invested at the start of the history would be worth, compared with the market. Computed from daily returns.</Explain>
            <Chart x={price.x} lines={price.lines} height={260} log format={(v) => v.toFixed(0)} />
          </Card>

          <Card title={`Past ranking (${modelLabel(overview.models[0])})`}>
            <Explain>
              The stock's percentile in the model's out-of-sample predictions: 1 is the top of the ranking, 0 the bottom. The history ends before the hold-out (
              {overview.holdout_start}).
            </Explain>
            <Chart x={ranks.x} lines={ranks.lines} height={180} format={(v) => v.toFixed(2)} baseline={0.5} />
          </Card>

          <div className="grid-2">
            <Card title="Features today">
              <Explain>Where the stock stands among all others on each feature: +0.5 is the highest value, −0.5 the lowest.</Explain>
              <Bars rows={detail.features.map(([f, v]) => ({ label: featureLabel(f), value: Number.isNaN(v) ? 0 : v }))} format={(v) => num(v)} max={0.5} />
            </Card>
            <Card title="Why this score">
              <Explain>
                How much each feature contributed to today's score (SHAP): green pushes the stock up the ranking, red pushes it down. The average score across all
                stocks is {num(detail.base_value, 4)}.
              </Explain>
              <Bars rows={detail.shap.slice(0, 10).map(([f, v]) => ({ label: featureLabel(f), value: v }))} format={(v) => (v * 1000).toFixed(1) + "‰"} />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
