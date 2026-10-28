// Data source (cloud URL or a local folder), theme and the current data snapshot.
import { useEffect, useState } from "react";

import { api, type Overview, type Settings } from "../api";
import type { SyncState } from "../App";
import { Card, ErrorBox, Explain } from "../components/ui";
import { applyTheme, savedTheme, type Theme } from "../theme";

interface Props {
  overview: Overview | null;
  sync: SyncState;
  onSync: () => Promise<void>;
  onChanged: () => void;
}

function syncText(sync: SyncState) {
  switch (sync.kind) {
    case "off":
      return "Cloud updates are off.";
    case "running":
      return "Checking for new data…";
    case "done": {
      const r = sync.report;
      const what = r.downloaded === 0 ? "nothing new" : `${r.downloaded} file(s), ${(r.downloaded_bytes / 1e6).toFixed(1)} MB`;
      return `Last check ${sync.at.toLocaleString()}: ${what}. Data as of ${r.data_date}.`;
    }
    case "failed":
      return `Last check ${sync.at.toLocaleString()} failed: ${sync.error}`;
  }
}

export function SettingsScreen({ overview, sync, onSync, onChanged }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [url, setUrl] = useState("");
  const [dir, setDir] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(savedTheme());

  const show = (s: Settings) => {
    setSettings(s);
    setUrl(s.data_url);
    setDir(s.custom_dir ? s.data_dir : "");
  };

  useEffect(() => {
    api.getSettings().then(show);
  }, []);

  const apply = (call: Promise<Settings>) =>
    call.then(
      (s) => {
        show(s);
        setError(null);
        onChanged();
      },
      (e) => setError(String(e)),
    );

  const changeTheme = (t: Theme) => {
    setTheme(t);
    applyTheme(t);
  };

  return (
    <div className="screen">
      <h1>Settings</h1>

      <Card title="Cloud data">
        <Explain>
          Every trading day the cloud pipeline publishes fresh rankings. The app downloads only the files that changed and keeps working offline on the last
          download.
        </Explain>
        <div className="toolbar">
          <input className="input grow" placeholder="https://….cloudfront.net" value={url} onChange={(e) => setUrl(e.target.value)} spellCheck={false} />
          <button className="btn" onClick={() => apply(api.setDataUrl(url))}>
            Save
          </button>
          <button className="btn ghost" disabled={!settings?.data_url || sync.kind === "running"} onClick={() => onSync()}>
            Update now
          </button>
          {settings?.data_url && (
            <button className="btn ghost" onClick={() => apply(api.setDataUrl(""))}>
              Turn off
            </button>
          )}
          {settings?.built_in_url && settings.data_url !== settings.built_in_url && (
            <button className="btn ghost" onClick={() => apply(api.setDataUrl("default"))}>
              Use built-in
            </button>
          )}
        </div>
        <div className={`muted small ${sync.kind === "failed" ? "neg" : ""}`}>{syncText(sync)}</div>
      </Card>

      <Card title="Data folder">
        <Explain>
          Where the app reads its files. Leave empty to use {settings?.data_url ? "the cloud download folder" : "the default folder"}; pick a folder created by{" "}
          <code>uv run python research/publish_local.py</code> to work without the cloud.
        </Explain>
        <div className="toolbar">
          <input className="input grow" placeholder={settings?.data_dir} value={dir} onChange={(e) => setDir(e.target.value)} spellCheck={false} />
          <button className="btn" onClick={() => apply(api.setDataDir(dir))}>
            Save
          </button>
          {settings?.custom_dir && (
            <button className="btn ghost" onClick={() => apply(api.setDataDir(""))}>
              Reset
            </button>
          )}
        </div>
        {settings && <div className="muted small">Reading from: {settings.data_dir}</div>}
      </Card>
      {error && <ErrorBox error={error} />}

      <Card title="Theme">
        <div className="segmented">
          {(
            [
              ["system", "system"],
              ["light", "light"],
              ["dark", "dark"],
            ] as [Theme, string][]
          ).map(([t, label]) => (
            <button key={t} className={theme === t ? "on" : ""} onClick={() => changeTheme(t)}>
              {label}
            </button>
          ))}
        </div>
      </Card>

      {overview && (
        <Card title="Current data snapshot">
          <table className="table compact">
            <tbody>
              <tr>
                <td>Data as of</td>
                <td>{overview.data_date}</td>
              </tr>
              <tr>
                <td>Published</td>
                <td>{overview.generated_at}</td>
              </tr>
              <tr>
                <td>Prediction history</td>
                <td>
                  {overview.history_start} — {overview.history_end}
                </td>
              </tr>
              <tr>
                <td>Hold-out (not used)</td>
                <td>from {overview.holdout_start}</td>
              </tr>
              <tr>
                <td>Stocks</td>
                <td>{overview.n_tickers}</td>
              </tr>
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
