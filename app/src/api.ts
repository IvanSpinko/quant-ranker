// Typed wrappers around the Rust commands in src-tauri/src/commands.rs.
import { invoke } from "@tauri-apps/api/core";

export type Weighting = "equal" | "inverse_vol";

export interface BacktestConfig {
  fraction: number;
  rebalance_every: number;
  lag: number;
  weighting: Weighting;
  vol_window: number;
  commission_bps: number;
  slippage_bps: number;
  smoothing_halflife: number;
  exit_fraction: number | null;
}

export interface Overview {
  data_dir: string;
  generated_at: string;
  data_date: string;
  history_start: string;
  history_end: string;
  holdout_start: string;
  models: string[];
  features: string[];
  default_backtest: BacktestConfig;
  n_tickers: number;
}

export interface Signal {
  ticker: string;
  name: string;
  sector: string;
  score: number;
  rank: number;
  pct: number;
}

/** Days since 1970-01-01 and values. */
export interface Series {
  t: number[];
  v: number[];
}

export interface StockDetail {
  ticker: string;
  name: string;
  sector: string;
  industry: string;
  signal: Signal | null;
  price: Series;
  market: Series;
  score_rank: Series;
  features: [string, number][];
  shap: [string, number][];
  base_value: number;
}

/** NaN values arrive as null. */
export interface Summary {
  cagr: number | null;
  volatility: number | null;
  sharpe: number | null;
  sortino: number | null;
  max_drawdown: number | null;
  calmar: number | null;
  annual_turnover: number | null;
  hit_rate: number | null;
  total_return: number | null;
  years: number | null;
}

export interface BacktestOutput {
  t: number[];
  equity_net: number[];
  equity_gross: number[];
  equity_market: number[];
  drawdown: number[];
  long_ann: number;
  short_ann: number;
  cost_ann: number;
  net: Summary | null;
  gross: Summary | null;
  market: Summary | null;
  yearly: { year: number; net: number; gross: number; market: number }[];
  rebalances: number;
  elapsed_ms: number;
}

export interface Quality {
  models: Record<string, string | number | null>[];
  ic: [string, Series][];
  importance: [string, number][];
}

export interface Settings {
  data_dir: string;
  default_dir: string;
  custom_dir: boolean;
  /** Effective cloud URL; empty when the cloud is off. */
  data_url: string;
  built_in_url: string | null;
}

export interface SyncReport {
  data_date: string;
  downloaded: number;
  downloaded_bytes: number;
  removed: number;
  files: number;
}

export const api = {
  overview: () => invoke<Overview>("overview"),
  signals: () => invoke<Signal[]>("signals"),
  stock: (ticker: string) => invoke<StockDetail>("stock", { ticker }),
  backtest: (model: string, config: BacktestConfig, start?: string, end?: string) =>
    invoke<BacktestOutput>("backtest", { request: { model, config, start: start || null, end: end || null } }),
  quality: () => invoke<Quality>("quality"),
  getSettings: () => invoke<Settings>("get_settings"),
  setDataDir: (dir: string) => invoke<Settings>("set_data_dir", { dir }),
  /** "" turns the cloud off, "default" restores the built-in URL. */
  setDataUrl: (url: string) => invoke<Settings>("set_data_url", { url }),
  syncData: () => invoke<SyncReport>("sync_data"),
};
