import { useCallback, useEffect, useState } from "react";

import { api, type Overview, type SyncReport } from "./api";
import { ErrorBox } from "./components/ui";
import { BacktestScreen } from "./screens/Backtest";
import { OverviewScreen } from "./screens/Overview";
import { QualityScreen } from "./screens/Quality";
import { SettingsScreen } from "./screens/Settings";
import { SignalsScreen } from "./screens/Signals";
import { StockScreen } from "./screens/Stock";

export type Screen = "overview" | "signals" | "stock" | "backtest" | "quality" | "settings";

export type SyncState =
  | { kind: "off" }
  | { kind: "running" }
  | { kind: "done"; report: SyncReport; at: Date }
  | { kind: "failed"; error: string; at: Date };

const NAV: { id: Screen; label: string; icon: string }[] = [
  { id: "overview", label: "How it works", icon: "◎" },
  { id: "signals", label: "Signals", icon: "≡" },
  { id: "stock", label: "Stock", icon: "◇" },
  { id: "backtest", label: "Backtest", icon: "↗" },
  { id: "quality", label: "Model quality", icon: "✓" },
  { id: "settings", label: "Settings", icon: "⚙" },
];

const time = (d: Date) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function SyncStatus({ sync }: { sync: SyncState }) {
  switch (sync.kind) {
    case "off":
      return <div>Local data folder</div>;
    case "running":
      return <div className="sync running">Checking for new data…</div>;
    case "done":
      return (
        <div className="sync ok" title={`${sync.report.downloaded} files downloaded`}>
          Up to date · {time(sync.at)}
        </div>
      );
    case "failed":
      return (
        <div className="sync failed" title={sync.error}>
          Offline · showing saved data
        </div>
      );
  }
}

export function App() {
  const [screen, setScreen] = useState<Screen>("overview");
  const [ticker, setTicker] = useState<string | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [sync, setSync] = useState<SyncState>({ kind: "off" });

  useEffect(() => {
    setError(null);
    api.overview().then(setOverview, (e) => {
      setOverview(null);
      setError(String(e));
    });
  }, [reload]);

  // Show the saved snapshot at once, then fetch what changed in the background.
  const runSync = useCallback(() => {
    setSync({ kind: "running" });
    return api.syncData().then(
      (report) => {
        setSync({ kind: "done", report, at: new Date() });
        if (report.downloaded > 0 || report.removed > 0) setReload((r) => r + 1);
      },
      (e) => {
        setSync({ kind: "failed", error: String(e), at: new Date() });
        setReload((r) => r + 1);
      },
    );
  }, []);

  useEffect(() => {
    api.getSettings().then((s) => {
      if (s.data_url) runSync();
    });
  }, [runSync]);

  const openStock = (t: string) => {
    setTicker(t);
    setScreen("stock");
  };

  const settingsChanged = () => {
    setReload((r) => r + 1);
    api.getSettings().then((s) => (s.data_url ? runSync() : setSync({ kind: "off" })));
  };

  let body;
  if (screen === "settings") {
    body = <SettingsScreen overview={overview} sync={sync} onSync={runSync} onChanged={settingsChanged} />;
  } else if (error || !overview) {
    body = error ? (
      <div className="screen">
        <h1>No data yet</h1>
        {sync.kind === "running" ? <div className="loading">Downloading data…</div> : <ErrorBox error={error} />}
        <p className="explain">
          The app reads published files: from the cloud when a data URL is set in Settings, or from a local folder created with{" "}
          <code>uv run python research/publish_local.py</code>.
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
          <SyncStatus sync={sync} />
          <div>Not investment advice</div>
        </div>
      </nav>
      <main className="content" key={reload}>
        {body}
      </main>
    </div>
  );
}
