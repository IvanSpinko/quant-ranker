// Interactive backtest: every change reruns the Rust engine on the out-of-sample history.
import { useEffect, useMemo, useState } from "react";

import { api, type BacktestConfig, type BacktestOutput, type Overview } from "../api";
import { Chart } from "../components/Chart";
import { Card, ErrorBox, Explain, Metric } from "../components/ui";
import { dayToSeconds, describeConfig, modelLabel, num, pct, signedPct, tone } from "../format";

interface Props {
  overview: Overview;
}

function Field({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return (
    <label className="field" title={hint}>
      <span className="field-label">
        {label} <span className="hint-dot">?</span>
      </span>
      {children}
    </label>
  );
}

function Choice<T extends string | number | null>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="segmented">
      {options.map(([v, label]) => (
        <button key={String(v)} type="button" className={v === value ? "on" : ""} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function BacktestScreen({ overview }: Props) {
  const [model, setModel] = useState(overview.models[0]);
  const [config, setConfig] = useState<BacktestConfig>(overview.default_backtest);
  const [start, setStart] = useState(overview.history_start);
  const [end, setEnd] = useState(overview.history_end);
  const [result, setResult] = useState<BacktestOutput | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    const timer = setTimeout(() => {
      api.backtest(model, config, start, end).then(
        (r) => {
          if (!cancelled) {
            setResult(r);
            setError(null);
            setBusy(false);
          }
        },
        (e) => {
          if (!cancelled) {
            setError(String(e));
            setBusy(false);
          }
        },
      );
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [model, config, start, end]);

  const set = <K extends keyof BacktestConfig>(key: K, value: BacktestConfig[K]) => setConfig((c) => ({ ...c, [key]: value }));

  const x = useMemo(() => (result ? result.t.map(dayToSeconds) : []), [result]);
  const equity = useMemo(
    () =>
      result
        ? [
            { label: "After costs", values: result.equity_net, color: "--c-net" },
            { label: "Before costs", values: result.equity_gross, color: "--c-gross", dash: true },
            { label: "Market (SPY)", values: result.equity_market, color: "--c-market", dash: true },
          ]
        : [],
    [result],
  );
  const drawdown = useMemo(() => (result ? [{ label: "Drawdown", values: result.drawdown, color: "--c-neg", fill: true }] : []), [result]);

  const net = result?.net;
  return (
    <div className="screen backtest">
      <h1>Backtest</h1>
      <Explain>
        What would have happened if the strategy had traded on the model's predictions from {overview.history_start} to {overview.history_end}. The
        predictions are honest (the model never saw the future), but the default parameters were tuned on 2014–2018, so treat 2019+ as the fairer test.
      </Explain>

      <div className="backtest-grid">
        <Card className="controls" title="Parameters">
          <Field label="Model" hint="Whose predictions drive the ranking">
            <select className="input" value={model} onChange={(e) => setModel(e.target.value)}>
              {overview.models.map((m) => (
                <option key={m} value={m}>
                  {modelLabel(m)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={`Long and short share: ${pct(config.fraction, 0)}`} hint="How many of the best stocks we buy, and as many of the worst we short">
            <input type="range" min={0.02} max={0.3} step={0.01} value={config.fraction} onChange={(e) => set("fraction", Number(e.target.value))} />
          </Field>
          <Field label="Holding buffer" hint="Keep a stock until it drops out of this share of the best (worst). Lowers turnover.">
            <Choice
              value={config.exit_fraction}
              options={[
                [null, "off"],
                [0.2, "20%"],
                [0.3, "30%"],
                [0.4, "40%"],
              ]}
              onChange={(v) => set("exit_fraction", v)}
            />
          </Field>
          <Field label="Score smoothing" hint="Average of past ranks: the longer the half-life, the less often the model changes its mind">
            <Choice
              value={config.smoothing_halflife}
              options={[
                [0, "off"],
                [3, "3 days"],
                [10, "10 days"],
                [20, "20 days"],
              ]}
              onChange={(v) => set("smoothing_halflife", v)}
            />
          </Field>
          <Field label="Rebalance" hint="How often we revisit what to hold">
            <Choice
              value={config.rebalance_every}
              options={[
                [1, "daily"],
                [5, "weekly"],
                [10, "2 weeks"],
                [21, "monthly"],
              ]}
              onChange={(v) => set("rebalance_every", v)}
            />
          </Field>
          <Field label="Weights" hint="Equal weights, or less money in more volatile stocks">
            <Choice
              value={config.weighting}
              options={[
                ["equal", "equal"],
                ["inverse_vol", "inverse vol"],
              ]}
              onChange={(v) => set("weighting", v)}
            />
          </Field>
          <Field label={`Costs: ${config.commission_bps + config.slippage_bps} bp`} hint="Commission plus slippage per dollar traded; 1 bp = 0.01%">
            <input
              type="range"
              min={0}
              max={30}
              step={1}
              value={config.commission_bps + config.slippage_bps}
              onChange={(e) => {
                const total = Number(e.target.value);
                setConfig((c) => ({ ...c, commission_bps: Math.min(1, total), slippage_bps: Math.max(0, total - 1) }));
              }}
            />
          </Field>
          <Field label="Period" hint="Limit the signal dates">
            <div className="row">
              <input className="input" type="date" min={overview.history_start} max={end} value={start} onChange={(e) => setStart(e.target.value)} />
              <input className="input" type="date" min={start} max={overview.history_end} value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
            <div className="row">
              <button className="btn small" type="button" onClick={() => (setStart(overview.history_start), setEnd("2018-12-31"))}>
                2014–2018
              </button>
              <button className="btn small" type="button" onClick={() => (setStart("2019-01-01"), setEnd(overview.history_end))}>
                2019+
              </button>
              <button className="btn small" type="button" onClick={() => (setStart(overview.history_start), setEnd(overview.history_end))}>
                All
              </button>
            </div>
          </Field>
          <button className="btn ghost" type="button" onClick={() => setConfig(overview.default_backtest)}>
            Reset to defaults
          </button>
        </Card>

        <div className="results">
          {error && <ErrorBox error={error} />}
          <Explain>Strategy: {describeConfig(config)}.</Explain>
          <div className={`metrics wide ${busy ? "busy" : ""}`}>
            <Metric label="Annual return" value={signedPct(net?.cagr)} tone={tone(net?.cagr)} hint="CAGR after costs" />
            <Metric label="Sharpe" value={num(net?.sharpe)} tone={tone(net?.sharpe)} hint="Return per unit of risk. Around 0.5 is the market's long-run level, above 1 is very good." />
            <Metric label="Max drawdown" value={pct(net?.max_drawdown)} tone="neg" hint="The largest fall in capital from a previous peak" />
            <Metric label="Turnover / year" value={result ? `${num(net?.annual_turnover, 0)}×` : "—"} hint="How many times a year the portfolio is traded (replacing every stock on both sides = 4)" />
            <Metric label="Costs / year" value={result ? pct(result.cost_ann) : "—"} hint="How much return commissions and slippage take" />
            <Metric label="Before costs" value={signedPct(result?.gross?.cagr)} hint="CAGR if trading were free" />
          </div>

          <Card
            title="Capital (1 = start)"
            aside={
              <label className="check">
                <input type="checkbox" checked={log} onChange={(e) => setLog(e.target.checked)} /> log scale
              </label>
            }
          >
            <Chart x={x} lines={equity} height={300} log={log} format={(v) => v.toFixed(2)} baseline={1} />
          </Card>
          <Card title="Drawdown">
            <Chart x={x} lines={drawdown} height={150} format={(v) => pct(v, 0)} />
          </Card>

          <div className="grid-2">
            <Card title="By year">
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Year</th>
                    <th className="num">After costs</th>
                    <th className="num">Before costs</th>
                    <th className="num">Market</th>
                  </tr>
                </thead>
                <tbody>
                  {result?.yearly.map((y) => (
                    <tr key={y.year}>
                      <td>{y.year}</td>
                      <td className={`num ${tone(y.net)}`}>{signedPct(y.net)}</td>
                      <td className={`num ${tone(y.gross)}`}>{signedPct(y.gross)}</td>
                      <td className="num muted">{signedPct(y.market)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <Card title="Where the return comes from">
              <Explain>A long/short book earns the difference: the longs should rise more than the shorts.</Explain>
              <div className="metrics">
                <Metric label="Long side / year" value={signedPct(result?.long_ann)} tone={tone(result?.long_ann)} />
                <Metric label="Short side / year" value={signedPct(result?.short_ann)} tone={tone(result?.short_ann)} hint="Positive when the shorted stocks fell" />
                <Metric label="Costs / year" value={result ? signedPct(-result.cost_ann) : "—"} tone="neg" />
              </div>
              <div className="muted small">{result && `${result.rebalances} rebalances · computed in ${result.elapsed_ms.toFixed(0)} ms in Rust`}</div>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
