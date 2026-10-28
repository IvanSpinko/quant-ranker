// Number and label formatting shared by the screens.
import type { BacktestConfig } from "./api";

export const pct = (v: number | null | undefined, digits = 1) =>
  v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(digits)}%`;

export const signedPct = (v: number | null | undefined, digits = 1) =>
  v == null || Number.isNaN(v) ? "—" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 2) =>
  v == null || Number.isNaN(v) ? "—" : v.toFixed(digits);

export const dayToSeconds = (day: number) => day * 86400;

export const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");

export const MODEL_LABELS: Record<string, string> = {
  lgbm: "LightGBM",
  ridge_lgbm: "Ridge + LightGBM",
  ridge: "Ridge",
  combo: "Momentum + reversal",
  momentum_12_1: "Momentum 12-1",
  reversal_5: "Reversal 5d",
};

export const FEATURE_LABELS: Record<string, string> = {
  mom_5: "Return, 5 days",
  mom_21: "Return, 1 month",
  mom_63: "Return, 3 months",
  mom_126: "Return, 6 months",
  mom_252: "Return, 1 year",
  mom_12_1: "Momentum 12-1",
  ret_1: "Return, last day",
  vol_21: "Volatility, 1 month",
  vol_63: "Volatility, 3 months",
  vol_ratio: "Volatility rising",
  volume_ratio: "Volume spike",
  dollar_volume: "Dollar volume",
  amihud: "Illiquidity (Amihud)",
  beta_126: "Beta to market",
  resid_21: "Return beyond beta",
  sector_rel_21: "Vs. sector",
};

/** One-sentence description of a backtest configuration. */
export function describeConfig(c: BacktestConfig) {
  const parts = [
    `buy the top ${pct(c.fraction, 0)} and short the bottom ${pct(c.fraction, 0)}`,
    c.exit_fraction && c.exit_fraction > c.fraction ? `keep a stock while it stays in the top (bottom) ${pct(c.exit_fraction, 0)}` : null,
    c.smoothing_halflife > 0 ? `scores smoothed with a ${c.smoothing_halflife}-day half-life` : null,
    `rebalance every ${c.rebalance_every} days`,
    `trade the day after the signal`,
    `costs of ${c.commission_bps + c.slippage_bps} bp per unit of turnover`,
  ];
  return parts.filter(Boolean).join("; ");
}

export const modelLabel = (m: string) => MODEL_LABELS[m] ?? m;
export const featureLabel = (f: string) => FEATURE_LABELS[f] ?? f;
