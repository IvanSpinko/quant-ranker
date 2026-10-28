// Data folder, theme and the current data snapshot.
import { useEffect, useState } from "react";

import { api, type Overview, type Settings } from "../api";
import { Card, ErrorBox, Explain } from "../components/ui";
import { applyTheme, savedTheme, type Theme } from "../theme";

interface Props {
  overview: Overview | null;
  onChanged: () => void;
}

export function SettingsScreen({ overview, onChanged }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [dir, setDir] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(savedTheme());

  useEffect(() => {
    api.getSettings().then((s) => {
      setSettings(s);
      setDir(s.data_dir);
    });
  }, []);

  const save = (value: string) => {
    api.setDataDir(value).then(
      (s) => {
        setSettings(s);
        setDir(s.data_dir);
        setError(null);
        onChanged();
      },
      (e) => setError(String(e)),
    );
  };

  const changeTheme = (t: Theme) => {
    setTheme(t);
    applyTheme(t);
  };

  return (
    <div className="screen">
      <h1>Settings</h1>
      <Card title="Data folder">
        <Explain>
          The folder with the published files (manifest.json and Parquet). Until the cloud pipeline exists, <code>uv run python research/publish_local.py</code>{" "}
          creates it.
        </Explain>
        <div className="toolbar">
          <input className="input grow" value={dir} onChange={(e) => setDir(e.target.value)} spellCheck={false} />
          <button className="btn" onClick={() => save(dir)}>
            Save
          </button>
          <button className="btn ghost" onClick={() => save("")}>
            Use default
          </button>
        </div>
        {settings && <div className="muted small">Default: {settings.default_dir}</div>}
        {error && <ErrorBox error={error} />}
      </Card>

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
