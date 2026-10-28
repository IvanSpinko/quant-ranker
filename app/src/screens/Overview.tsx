// The whole pipeline on one page, with the real numbers behind each step.
import { useEffect, useMemo, useState } from "react";

import { api, type BacktestOutput, type Overview, type Signal } from "../api";
import type { Screen } from "../App";
import { Chart } from "../components/Chart";
import { Metric } from "../components/ui";
import { dayToSeconds, describeConfig, featureLabel, modelLabel, num, pct, signedPct, tone } from "../format";

interface Props {
  overview: Overview;
  go: (s: Screen) => void;
  openStock: (ticker: string) => void;
}

function Step({ n, title, text, children, action }: { n: number; title: string; text: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="step">
      <div className="step-rail">
        <div className="step-num">{n}</div>
        <div className="step-line" />
      </div>
      <div className="step-body card">
        <div className="step-text">
          <h3>{title}</h3>
          <p>{text}</p>
          {action}
        </div>
        <div className="step-data">{children}</div>
      </div>
    </div>
  );
}

export function OverviewScreen({ overview, go, openStock }: Props) {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [result, setResult] = useState<BacktestOutput | null>(null);
  const model = overview.models[0];

  useEffect(() => {
    api.signals().then(setSignals, () => setSignals([]));
    api.backtest(model, overview.default_backtest).then(setResult, () => setResult(null));
  }, [model, overview.default_backtest]);

  const x = useMemo(() => (result ? result.t.map(dayToSeconds) : []), [result]);
  const lines = useMemo(
    () =>
      result
        ? [
            { label: "Strategy after costs", values: result.equity_net, color: "--c-net" },
            { label: "Before costs", values: result.equity_gross, color: "--c-gross", dash: true },
          ]
        : [],
    [result],
  );

  const top = signals.slice(0, 5);
  const bottom = signals.slice(-5).reverse();
  const config = overview.default_backtest;

  return (
    <div className="screen">
      <h1>How it works</h1>
      <p className="lead">
        Every trading day the S&P 500 stocks are ranked by their expected return over the next 5 days relative to the market. Below is the whole path
        from prices to strategy results, with the real numbers from the current data.
      </p>

      <Step
        n={1}
        title="Data"
        text="Daily prices and trading volumes, adjusted for splits and dividends. The app never receives raw prices, only derived data: returns, ranks and predictions."
      >
        <div className="metrics">
          <Metric label="Stocks" value={String(overview.n_tickers)} />
          <Metric label="Data as of" value={overview.data_date} />
          <Metric label="Published" value={overview.generated_at.slice(0, 10)} />
        </div>
      </Step>

      <Step
        n={2}
        title="Features"
        text="For every stock and every day we compute numbers that describe its past behaviour. Stocks are then compared with each other: each value becomes a position in the line-up, from −0.5 (lowest) to +0.5 (highest)."
      >
        <div className="chips">
          {overview.features.map((f) => (
            <span className="chip" key={f} title={f}>
              {featureLabel(f)}
            </span>
          ))}
        </div>
      </Step>

      <Step
        n={3}
        title="Model"
        text="The model learns how today's features relate to the return over the next 5 days. It trains only on the past and is retrained every quarter (walk-forward), so its history consists of honest, blind predictions."
        action={
          <button className="link" onClick={() => go("quality")}>
            Model quality →
          </button>
        }
      >
        <div className="metrics">
          <Metric label="Main model" value={modelLabel(model)} />
          <Metric label="Out-of-sample predictions" value={`${overview.history_start.slice(0, 4)}–${overview.history_end.slice(0, 4)}`} />
          <Metric label="Hold-out" value={`from ${overview.holdout_start}`} hint="The latest period is used neither for training nor for tuning: it is the final exam." />
        </div>
      </Step>

      <Step
        n={4}
        title="Today's ranking"
        text="The model gives every stock a score and the stocks are sorted. At the top are candidates to buy, at the bottom candidates to short."
        action={
          <button className="link" onClick={() => go("signals")}>
            Full ranking →
          </button>
        }
      >
        <div className="split">
          <div>
            <div className="split-title pos">Top</div>
            {top.map((s) => (
              <button className="row-link" key={s.ticker} onClick={() => openStock(s.ticker)}>
                <span className="rank">#{s.rank}</span> <b>{s.ticker}</b> <span className="muted">{s.name}</span>
              </button>
            ))}
          </div>
          <div>
            <div className="split-title neg">Bottom</div>
            {bottom.map((s) => (
              <button className="row-link" key={s.ticker} onClick={() => openStock(s.ticker)}>
                <span className="rank">#{s.rank}</span> <b>{s.ticker}</b> <span className="muted">{s.name}</span>
              </button>
            ))}
          </div>
        </div>
      </Step>

      <Step n={5} title="Portfolio" text={`The ranking becomes a long/short portfolio: ${describeConfig(config)}.`}>
        <div className="metrics">
          <Metric label="Long" value={pct(config.fraction, 0)} hint="Share of stocks with the best scores that we buy" />
          <Metric label="Short" value={pct(config.fraction, 0)} hint="Share of stocks with the worst scores that we sell short" />
          <Metric label="Holding buffer" value={config.exit_fraction ? pct(config.exit_fraction, 0) : "off"} />
          <Metric label="Smoothing" value={config.smoothing_halflife > 0 ? `${config.smoothing_halflife} days` : "off"} />
        </div>
      </Step>

      <Step
        n={6}
        title="Historical result"
        text="The backtest runs this portfolio over the model's past predictions, including commissions and slippage. The app computes it itself, in a fraction of a second."
        action={
          <button className="link" onClick={() => go("backtest")}>
            Change parameters →
          </button>
        }
      >
        {result ? (
          <>
            <div className="metrics">
              <Metric label="Annual return" value={signedPct(result.net?.cagr)} tone={tone(result.net?.cagr)} hint="CAGR after costs" />
              <Metric label="Sharpe" value={num(result.net?.sharpe)} hint="Return per unit of risk; around 0.5 is the market's long-run level" />
              <Metric label="Max drawdown" value={pct(result.net?.max_drawdown)} tone="neg" />
            </div>
            <Chart x={x} lines={lines} height={180} log format={(v) => v.toFixed(1)} baseline={1} />
          </>
        ) : (
          <div className="loading">Computing…</div>
        )}
      </Step>
    </div>
  );
}
