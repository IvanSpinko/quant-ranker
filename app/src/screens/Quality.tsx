// How good the signal is: IC by model, IC over time and what LightGBM relies on.
import { useEffect, useMemo, useState } from "react";

import { api, type Quality } from "../api";
import { Chart, type Line } from "../components/Chart";
import { Bars, Card, ErrorBox, Explain } from "../components/ui";
import { dayToSeconds, featureLabel, modelLabel, num, pct, signedPct, tone } from "../format";

const COLORS = ["--c-net", "--c-accent", "--c-gross", "--c-market", "--c-5", "--c-6"];

export function QualityScreen() {
  const [quality, setQuality] = useState<Quality | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.quality().then(setQuality, (e) => setError(String(e)));
  }, []);

  const ic = useMemo(() => {
    if (!quality || quality.ic.length === 0) return { x: [], lines: [] as Line[] };
    // All models share the walk-forward dates; align on the first model's dates.
    const base = quality.ic[0][1].t;
    return {
      x: base.map(dayToSeconds),
      lines: quality.ic.map(([model, s], i) => {
        const byDay = new Map(s.t.map((t, k) => [t, s.v[k]]));
        return { label: modelLabel(model), values: base.map((t) => byDay.get(t) ?? null), color: COLORS[i % COLORS.length] };
      }),
    };
  }, [quality]);

  const get = (row: Record<string, string | number | null>, key: string) => row[key] as number | null;

  return (
    <div className="screen">
      <h1>Model quality</h1>
      <Explain>
        IC (information coefficient) is the correlation between the model's ranking and what actually happened 5 days later. 0 is guessing, 1 is a perfect
        ordering. In finance an IC of 0.02–0.05 already counts as good. All numbers are out of sample, from the walk-forward runs.
      </Explain>
      {error && <ErrorBox error={error} />}
      {quality && (
        <>
          <Card title="Model comparison (stage 1 backtest settings: no buffer, no smoothing)">
            <div className="table-wrap">
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th className="num" title="Mean daily IC">
                      IC
                    </th>
                    <th className="num" title="IC divided by its spread: how stable the signal is">
                      IC IR
                    </th>
                    <th className="num" title="Share of months with a positive mean IC">
                      Months IC &gt; 0
                    </th>
                    <th className="num">Return/yr before costs</th>
                    <th className="num">After costs</th>
                    <th className="num">Sharpe after costs</th>
                  </tr>
                </thead>
                <tbody>
                  {quality.models.map((row) => (
                    <tr key={String(row.model)}>
                      <td>
                        <b>{modelLabel(String(row.model))}</b>
                      </td>
                      <td className="num">{num(get(row, "ic_mean_ic"), 4)}</td>
                      <td className="num">{num(get(row, "ic_ic_ir"), 3)}</td>
                      <td className="num">{pct(get(row, "ic_pct_positive_months"), 0)}</td>
                      <td className={`num ${tone(get(row, "gross_cagr"))}`}>{signedPct(get(row, "gross_cagr"))}</td>
                      <td className={`num ${tone(get(row, "net_cagr"))}`}>{signedPct(get(row, "net_cagr"))}</td>
                      <td className={`num ${tone(get(row, "net_sharpe"))}`}>{num(get(row, "net_sharpe"))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="IC over time (rolling one-year mean)">
            <Explain>Above zero, the model ordered stocks better than chance on average. Dips below zero are periods when the market behaved the other way round.</Explain>
            <Chart x={ic.x} lines={ic.lines} height={280} format={(v) => v.toFixed(3)} baseline={0} />
          </Card>

          <Card title="What LightGBM relies on">
            <Explain>Each feature's share of the improvement in predictions (gain), averaged over all retrains.</Explain>
            <Bars rows={quality.importance.map(([f, v]) => ({ label: featureLabel(f), value: v }))} format={(v) => pct(v)} />
          </Card>
        </>
      )}
    </div>
  );
}
