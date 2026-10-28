import { useEffect, useState } from "react";

import { api, type Overview } from "./api";
import { ErrorBox } from "./components/ui";
import { BacktestScreen } from "./screens/Backtest";
import { OverviewScreen } from "./screens/Overview";
import { QualityScreen } from "./screens/Quality";
import { SettingsScreen } from "./screens/Settings";
import { SignalsScreen } from "./screens/Signals";
import { StockScreen } from "./screens/Stock";

export type Screen = "overview" | "signals" | "stock" | "backtest" | "quality" | "settings";

const NAV: { id: Screen; label: string; icon: string }[] = [
  { id: "overview", label: "How it works", icon: "◎" },
  { id: "signals", label: "Signals", icon: "≡" },
  { id: "stock", label: "Stock", icon: "◇" },
  { id: "backtest", label: "Backtest", icon: "↗" },
  { id: "quality", label: "Model quality", icon: "✓" },
  { id: "settings", label: "Settings", icon: "⚙" },
];

export function App() {
  const [screen, setScreen] = useState<Screen>("overview");
  const [ticker, setTicker] = useState<string | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setError(null);
    api.overview().then(setOverview, (e) => {
      setOverview(null);
      setError(String(e));
    });
  }, [reload]);

  const openStock = (t: string) => {
    setTicker(t);
    setScreen("stock");
  };

  let body;
  if (screen === "settings") {
    body = <SettingsScreen overview={overview} onChanged={() => setReload((r) => r + 1)} />;
  } else if (error || !overview) {
    body = error ? (
      <div className="screen">
        <h1>No data</h1>
        <ErrorBox error={error} />
        <p className="explain">
          The app reads published files. Generate them with <code>uv run python research/publish_local.py</code> or choose a folder in Settings.
        </p>
      </div>
    ) : (
      <div className="loading">Loading…</div>
    );
  } else {
    body = {
      overview: <OverviewScreen overview={overview} go={setScreen} openStock={openStock} />,
      signals: <SignalsScreen overview={overview} openStock={openStock} />,
      stock: <StockScreen overview={overview} ticker={ticker} openStock={openStock} />,
      backtest: <BacktestScreen overview={overview} />,
      quality: <QualityScreen />,
    }[screen];
  }

  return (
    <div className="layout">
      <nav className="sidebar">
        <div className="brand">
          <span className="brand-mark" />
          Quant Ranker
        </div>
        {NAV.map((n) => (
          <button key={n.id} className={`nav ${screen === n.id ? "active" : ""}`} onClick={() => setScreen(n.id)}>
            <span className="nav-icon">{n.icon}</span>
            {n.label}
          </button>
        ))}
        <div className="sidebar-foot">
          {overview && <div>Data as of {overview.data_date}</div>}
          <div>Not investment advice</div>
        </div>
      </nav>
      <main className="content">{body}</main>
    </div>
  );
}
