// Today's ranking with search and a sector filter.
import { useEffect, useMemo, useState } from "react";

import { api, type Overview, type Signal } from "../api";
import { ErrorBox, Explain } from "../components/ui";

interface Props {
  overview: Overview;
  openStock: (ticker: string) => void;
}

export function SignalsScreen({ overview, openStock }: Props) {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("");

  useEffect(() => {
    api.signals().then(setSignals, (e) => setError(String(e)));
  }, []);

  const sectors = useMemo(() => [...new Set(signals.map((s) => s.sector))].sort(), [signals]);
  const zone = Math.floor(signals.length * overview.default_backtest.fraction);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return signals.filter((s) => (!sector || s.sector === sector) && (!q || s.ticker.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)));
  }, [signals, query, sector]);

  return (
    <div className="screen">
      <h1>Signals</h1>
      <Explain>
        Ranking as of {overview.data_date}: rank 1 has the highest expected 5-day return relative to the market. Green marks the stocks the strategy buys (top{" "}
        {zone}), red the ones it shorts (bottom {zone}). Click a row to open the stock.
      </Explain>
      {error && <ErrorBox error={error} />}
      <div className="toolbar">
        <input className="input" placeholder="Ticker or company" value={query} onChange={(e) => setQuery(e.target.value)} />
        <select className="input" value={sector} onChange={(e) => setSector(e.target.value)}>
          <option value="">All sectors</option>
          {sectors.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <span className="muted">{rows.length} stocks</span>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th className="num">Rank</th>
              <th>Ticker</th>
              <th>Company</th>
              <th>Sector</th>
              <th title="Position in the ranking: 100% is the best, 0% the worst">Percentile</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const side = s.rank <= zone ? "long" : s.rank > signals.length - zone ? "short" : "";
              return (
                <tr key={s.ticker} className={`clickable ${side}`} onClick={() => openStock(s.ticker)}>
                  <td className="num">{s.rank}</td>
                  <td>
                    <b>{s.ticker}</b>
                  </td>
                  <td>{s.name}</td>
                  <td className="muted">{s.sector}</td>
                  <td>
                    <div className="pct-bar">
                      <span style={{ width: `${s.pct * 100}%` }} />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
