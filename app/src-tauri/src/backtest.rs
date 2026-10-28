//! Long/short backtest, a port of `quant_core.backtest` (the Python reference).
//!
//! Timing: scores known at the close of signal date t, traded at the close of t + lag, earning
//! returns from the next day on. Target weights are held constant between rebalances. The long
//! side sums to +1 and the short side to -1. Costs are turnover times commission plus slippage.
//! The golden test in `tests/golden.rs` checks this module against the Python outputs.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde::{Deserialize, Serialize};

use crate::dates::year_of;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Weighting {
    Equal,
    InverseVol,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub fraction: f64,
    pub rebalance_every: usize,
    pub lag: usize,
    pub weighting: Weighting,
    pub vol_window: usize,
    pub commission_bps: f64,
    pub slippage_bps: f64,
    #[serde(default)]
    pub smoothing_halflife: f64,
    #[serde(default)]
    pub exit_fraction: Option<f64>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            fraction: 0.1,
            rebalance_every: 5,
            lag: 1,
            weighting: Weighting::Equal,
            vol_window: 21,
            commission_bps: 1.0,
            slippage_bps: 5.0,
            smoothing_halflife: 0.0,
            exit_fraction: None,
        }
    }
}

impl Config {
    fn cost_rate(&self) -> f64 {
        (self.commission_bps + self.slippage_bps) / 1e4
    }
}

/// Daily returns on a dense grid: `ret[day * n_tickers + col]`, NaN where a ticker has no bar.
#[derive(Debug, Clone)]
pub struct Returns {
    pub dates: Vec<i32>,
    pub tickers: Vec<String>,
    pub ret: Vec<f64>,
    index: HashMap<String, usize>,
}

impl Returns {
    /// Build from long rows (date, ticker, ret). Dates and tickers are sorted.
    pub fn from_rows(rows: impl IntoIterator<Item = (i32, String, f64)>) -> Self {
        let rows: Vec<_> = rows.into_iter().collect();
        let mut dates: Vec<i32> = rows.iter().map(|r| r.0).collect();
        dates.sort_unstable();
        dates.dedup();
        let mut tickers: Vec<String> = rows.iter().map(|r| r.1.clone()).collect();
        tickers.sort();
        tickers.dedup();
        let day: HashMap<i32, usize> = dates.iter().enumerate().map(|(i, d)| (*d, i)).collect();
        let index: HashMap<String, usize> = tickers.iter().enumerate().map(|(i, t)| (t.clone(), i)).collect();
        let n = tickers.len();
        let mut ret = vec![f64::NAN; dates.len() * n];
        for (date, ticker, value) in rows {
            ret[day[&date] * n + index[&ticker]] = value;
        }
        Self { dates, tickers, ret, index }
    }

    pub fn col(&self, ticker: &str) -> Option<usize> {
        self.index.get(ticker).copied()
    }

    pub fn n_days(&self) -> usize {
        self.dates.len()
    }

    pub fn n_tickers(&self) -> usize {
        self.tickers.len()
    }

    pub fn get(&self, day: usize, col: usize) -> f64 {
        self.ret[day * self.tickers.len() + col]
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Score {
    pub date: i32,
    pub ticker: String,
    pub score: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct DailyRow {
    pub date: i32,
    pub gross: f64,
    pub cost: f64,
    pub net: f64,
    pub turnover: f64,
    pub long: f64,
    pub short: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct WeightRow {
    pub signal_date: i32,
    pub ticker: String,
    pub weight: f64,
}

#[derive(Debug, Clone)]
pub struct BacktestResult {
    pub daily: Vec<DailyRow>,
    /// Target weights at each rebalance, sorted by signal date then ticker.
    pub weights: Vec<WeightRow>,
}

/// Rank per date scaled to [-0.5, 0.5] (average rank for ties); dates with one name are dropped.
fn cross_sectional_rank(scores: &[Score]) -> Vec<Score> {
    let mut by_date: BTreeMap<i32, Vec<&Score>> = BTreeMap::new();
    for s in scores {
        by_date.entry(s.date).or_default().push(s);
    }
    let mut out = Vec::with_capacity(scores.len());
    for rows in by_date.values() {
        let n = rows.len();
        if n < 2 {
            continue;
        }
        let mut order: Vec<usize> = (0..n).collect();
        order.sort_by(|&a, &b| rows[a].score.total_cmp(&rows[b].score));
        let mut ranks = vec![0.0; n];
        let mut i = 0;
        while i < n {
            let mut j = i;
            while j + 1 < n && rows[order[j + 1]].score == rows[order[i]].score {
                j += 1;
            }
            let average = (i + j) as f64 / 2.0 + 1.0;
            for k in i..=j {
                ranks[order[k]] = average;
            }
            i = j + 1;
        }
        for (k, row) in rows.iter().enumerate() {
            out.push(Score { date: row.date, ticker: row.ticker.clone(), score: (ranks[k] - 1.0) / (n - 1) as f64 - 0.5 });
        }
    }
    out
}

/// Per-date ranks averaged exponentially over each ticker's past dates (adjusted EWM, half-life in dates).
pub fn smooth_scores(scores: &[Score], halflife: f64) -> Vec<Score> {
    if halflife <= 0.0 {
        return scores.to_vec();
    }
    let mut ranked = cross_sectional_rank(scores);
    ranked.sort_by(|a, b| a.ticker.cmp(&b.ticker).then(a.date.cmp(&b.date)));
    let decay = (-(2f64.ln()) / halflife).exp();
    let mut out = Vec::with_capacity(ranked.len());
    let (mut num, mut den) = (0.0, 0.0);
    for (i, s) in ranked.iter().enumerate() {
        if i == 0 || ranked[i - 1].ticker != s.ticker {
            num = 0.0;
            den = 0.0;
        }
        num = s.score + decay * num;
        den = 1.0 + decay * den;
        out.push(Score { date: s.date, ticker: s.ticker.clone(), score: num / den });
    }
    out
}

/// Rolling standard deviation (ddof 1) over each ticker's own return rows; NaN until the window fills.
fn rolling_vol(returns: &Returns, window: usize) -> Vec<f64> {
    let (n_days, n) = (returns.n_days(), returns.n_tickers());
    let mut vol = vec![f64::NAN; n_days * n];
    if window < 2 {
        return vol;
    }
    for col in 0..n {
        let mut recent: Vec<f64> = Vec::new();
        for day in 0..n_days {
            let r = returns.get(day, col);
            if r.is_nan() {
                continue;
            }
            recent.push(r);
            if recent.len() >= window {
                let w = &recent[recent.len() - window..];
                let mean = w.iter().sum::<f64>() / window as f64;
                let var = w.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (window - 1) as f64;
                vol[day * n + col] = var.sqrt();
            }
        }
    }
    vol
}

struct Candidate {
    ticker: String,
    score: f64,
    vol: f64,
}

/// Names sorted best first (score descending, ticker ascending on ties).
fn best_first(rows: &mut [Candidate]) {
    rows.sort_by(|a, b| b.score.total_cmp(&a.score).then_with(|| a.ticker.cmp(&b.ticker)));
}

fn median(values: &mut [f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(f64::total_cmp);
    let mid = values.len() / 2;
    Some(if values.len() % 2 == 1 { values[mid] } else { (values[mid - 1] + values[mid]) / 2.0 })
}

/// Weights of one side: equal, or proportional to 1 / vol with missing vols set to the side median.
fn side_weights(names: &[&Candidate], sign: f64, weighting: Weighting) -> Vec<(String, f64)> {
    let raw: Vec<f64> = match weighting {
        Weighting::Equal => vec![1.0; names.len()],
        Weighting::InverseVol => {
            let valid = |v: f64| (v > 0.0).then_some(v);
            let mut known: Vec<f64> = names.iter().filter_map(|c| valid(c.vol)).collect();
            let fill = median(&mut known);
            names.iter().map(|c| valid(c.vol).or(fill).map_or(1.0, |v| 1.0 / v)).collect()
        }
    };
    let total: f64 = raw.iter().sum();
    names.iter().zip(raw).map(|(c, r)| (c.ticker.clone(), sign * r / total)).collect()
}

/// Holding buffer for one side: keep held names ranked inside `band`, fill up to `m` with the
/// best other names. `ranked` is best first for this side; `held` is updated in place.
fn keep_or_replace<'a>(
    ranked: &[&'a Candidate],
    band: usize,
    m: usize,
    held: &mut HashSet<String>,
    exclude: &HashSet<String>,
) -> Vec<&'a Candidate> {
    let keep: Vec<&Candidate> = ranked[..band].iter().copied().filter(|c| held.contains(&c.ticker)).collect();
    let kept: HashSet<&str> = keep.iter().map(|c| c.ticker.as_str()).collect();
    let new: Vec<&Candidate> = ranked
        .iter()
        .copied()
        .filter(|c| !kept.contains(c.ticker.as_str()) && !exclude.contains(&c.ticker))
        .take(m - keep.len())
        .collect();
    let chosen: Vec<&Candidate> = keep.into_iter().chain(new).collect();
    *held = chosen.iter().map(|c| c.ticker.clone()).collect();
    chosen
}

/// Long the top `fraction` and short the bottom `fraction` on every rebalance date, with an
/// optional holding buffer. `dates` must be the rebalance dates in order.
fn target_weights(dates: &[(i32, Vec<Candidate>)], config: &Config) -> Vec<WeightRow> {
    let buffered = config.exit_fraction.is_some_and(|e| e > config.fraction);
    let exit_fraction = config.exit_fraction.unwrap_or(config.fraction);
    let mut held: [HashSet<String>; 2] = [HashSet::new(), HashSet::new()];
    let mut out = Vec::new();
    for (date, rows) in dates {
        let mut order: Vec<&Candidate> = rows.iter().collect();
        order.sort_by(|a, b| b.score.total_cmp(&a.score).then_with(|| a.ticker.cmp(&b.ticker)));
        let n = order.len();
        let m = ((n as f64 * config.fraction).floor() as usize).max(1);
        if n < 2 * m {
            held = [HashSet::new(), HashSet::new()];
            continue;
        }
        let (long, short): (Vec<&Candidate>, Vec<&Candidate>) = if buffered {
            let mut band = ((n as f64 * exit_fraction).floor() as usize).max(m);
            if n < 2 * band {
                band = m;
            }
            let [held_long, held_short] = &mut held;
            let long = keep_or_replace(&order, band, m, held_long, &HashSet::new());
            let long_names: HashSet<String> = long.iter().map(|c| c.ticker.clone()).collect();
            let reversed: Vec<&Candidate> = order.iter().rev().copied().collect();
            let short = keep_or_replace(&reversed, band, m, held_short, &long_names);
            (long, short)
        } else {
            (order[..m].to_vec(), order[n - m..].to_vec())
        };
        let mut rows: Vec<WeightRow> = side_weights(&long, 1.0, config.weighting)
            .into_iter()
            .chain(side_weights(&short, -1.0, config.weighting))
            .map(|(ticker, weight)| WeightRow { signal_date: *date, ticker, weight })
            .collect();
        rows.sort_by(|a, b| a.ticker.cmp(&b.ticker));
        out.extend(rows);
    }
    out
}

pub fn run_backtest(scores: &[Score], returns: &Returns, config: &Config) -> BacktestResult {
    let (n_days, n) = (returns.n_days(), returns.n_tickers());
    let day_of: HashMap<i32, usize> = returns.dates.iter().enumerate().map(|(i, d)| (*d, i)).collect();
    let step = config.rebalance_every.max(1);

    let scores: Vec<Score> = scores.iter().filter(|s| !s.score.is_nan()).cloned().collect();
    let scores = smooth_scores(&scores, config.smoothing_halflife);

    let mut signal_days: Vec<usize> = scores.iter().filter_map(|s| day_of.get(&s.date).copied()).collect();
    signal_days.sort_unstable();
    signal_days.dedup();
    let rebalance_days: Vec<usize> = signal_days.iter().copied().step_by(step).collect();
    let is_rebalance: HashSet<usize> = rebalance_days.iter().copied().collect();

    let vol = match config.weighting {
        Weighting::InverseVol => rolling_vol(returns, config.vol_window),
        Weighting::Equal => Vec::new(),
    };
    let mut by_day: BTreeMap<usize, Vec<Candidate>> = rebalance_days.iter().map(|d| (*d, Vec::new())).collect();
    for s in &scores {
        let Some(&day) = day_of.get(&s.date) else { continue };
        if !is_rebalance.contains(&day) {
            continue;
        }
        let v = match (config.weighting, returns.col(&s.ticker)) {
            (Weighting::InverseVol, Some(col)) => vol[day * n + col],
            _ => f64::NAN,
        };
        by_day.get_mut(&day).unwrap().push(Candidate { ticker: s.ticker.clone(), score: s.score, vol: v });
    }
    let mut dated: Vec<(i32, Vec<Candidate>)> =
        by_day.into_iter().map(|(day, mut rows)| (returns.dates[day], { best_first(&mut rows); rows })).collect();
    dated.sort_by_key(|(date, _)| *date);
    let weights = target_weights(&dated, config);

    let mut trade_days: Vec<usize> = rebalance_days.iter().map(|d| d + config.lag).filter(|d| *d < n_days).collect();
    trade_days.dedup();
    let mut target = vec![0.0; n_days * n];
    for w in &weights {
        let day = day_of[&w.signal_date] + config.lag;
        if let (true, Some(col)) = (day < n_days, returns.col(&w.ticker)) {
            target[day * n + col] = w.weight;
        }
    }
    let is_trade: HashSet<usize> = trade_days.iter().copied().collect();

    let mut daily = Vec::with_capacity(n_days);
    let mut held = vec![0.0; n];
    for day in 0..n_days {
        let positions: &[f64] = if is_trade.contains(&day) { &target[day * n..(day + 1) * n] } else { &held };
        let (mut gross, mut long, mut short, mut turnover) = (0.0, 0.0, 0.0, 0.0);
        for col in 0..n {
            let r = returns.get(day, col);
            let r = if r.is_nan() { 0.0 } else { r };
            let h = held[col];
            gross += h * r;
            if h > 0.0 {
                long += h * r;
            } else if h < 0.0 {
                short += h * r;
            }
            turnover += (positions[col] - h).abs();
        }
        let cost = turnover * config.cost_rate();
        daily.push(DailyRow { date: returns.dates[day], gross, cost, net: gross - cost, turnover, long, short });
        if is_trade.contains(&day) {
            held.copy_from_slice(&target[day * n..(day + 1) * n]);
        }
    }
    let daily = match (trade_days.first(), trade_days.last()) {
        (Some(&first), Some(&last)) => {
            let end = (last + step).min(n_days - 1);
            daily[first..=end].to_vec()
        }
        _ => Vec::new(),
    };
    BacktestResult { daily, weights }
}

#[derive(Debug, Clone, Serialize)]
pub struct Summary {
    pub cagr: f64,
    pub volatility: f64,
    pub sharpe: f64,
    pub sortino: f64,
    pub max_drawdown: f64,
    pub calmar: f64,
    pub annual_turnover: f64,
    pub hit_rate: f64,
    pub total_return: f64,
    pub years: f64,
}

/// Same definitions as `performance_summary` in Python; `None` for an empty series.
pub fn performance_summary(returns: &[f64], turnover: &[f64], periods_per_year: f64) -> Option<Summary> {
    let n = returns.len();
    if n == 0 {
        return None;
    }
    let nf = n as f64;
    let mut equity = 1.0;
    let mut peak: f64 = 1.0;
    let mut max_drawdown: f64 = 0.0;
    for r in returns {
        equity *= 1.0 + r;
        peak = peak.max(equity);
        max_drawdown = max_drawdown.min(equity / peak - 1.0);
    }
    let years = nf / periods_per_year;
    let cagr = if equity > 0.0 { equity.powf(1.0 / years) - 1.0 } else { -1.0 };
    let mean = returns.iter().sum::<f64>() / nf;
    let std = if n > 1 {
        (returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / (nf - 1.0)).sqrt()
    } else {
        f64::NAN
    };
    let downside = (returns.iter().map(|r| r.min(0.0).powi(2)).sum::<f64>() / nf).sqrt();
    let annualise = periods_per_year.sqrt();
    let ratio = |num: f64, den: f64| if den != 0.0 && !den.is_nan() { num / den * annualise } else { f64::NAN };
    Some(Summary {
        cagr,
        volatility: std * annualise,
        sharpe: ratio(mean, std),
        sortino: ratio(mean, downside),
        max_drawdown,
        calmar: if max_drawdown < 0.0 { cagr / -max_drawdown } else { f64::NAN },
        annual_turnover: turnover.iter().sum::<f64>() / nf * periods_per_year,
        hit_rate: returns.iter().filter(|r| **r > 0.0).count() as f64 / nf,
        total_return: equity - 1.0,
        years,
    })
}

/// Compounded return per calendar year.
pub fn yearly_returns(dates: &[i32], returns: &[f64]) -> Vec<(i32, f64)> {
    let mut years: BTreeMap<i32, f64> = BTreeMap::new();
    for (date, r) in dates.iter().zip(returns) {
        *years.entry(year_of(*date)).or_insert(1.0) *= 1.0 + r;
    }
    years.into_iter().map(|(y, g)| (y, g - 1.0)).collect()
}
