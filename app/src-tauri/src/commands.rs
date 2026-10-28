//! Tauri commands called by the web UI. Heavy work runs on a blocking thread.

use std::path::PathBuf;
use std::sync::{Arc, Mutex, RwLock};
use std::time::Instant;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, State};

use crate::backtest::{Config, Summary, performance_summary, run_backtest, yearly_returns};
use crate::data::{Result, Signal, Store};
use crate::dates::parse_iso;
use crate::sync::{HttpSource, SyncReport, sync};

pub struct AppState {
    store: RwLock<Arc<Store>>,
    settings: Mutex<Settings>,
    settings_path: PathBuf,
    /// Local folder used when no data URL is configured.
    default_dir: PathBuf,
    /// Where cloud downloads are kept.
    cache_dir: PathBuf,
}

/// Saved in the app config folder. data_url: None = built-in URL, Some("") = cloud switched off.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct Settings {
    data_dir: Option<PathBuf>,
    #[serde(default)]
    data_url: Option<String>,
}

/// Published-data URL baked in at build time (QR_DATA_URL), if any.
const BUILT_IN_URL: Option<&str> = option_env!("QR_DATA_URL");

impl Settings {
    fn url(&self) -> Option<String> {
        match &self.data_url {
            Some(url) if url.trim().is_empty() => None,
            Some(url) => Some(url.trim().to_string()),
            None => BUILT_IN_URL.map(str::to_string),
        }
    }
}

impl AppState {
    pub fn new(app: &AppHandle) -> Self {
        let config_dir = app.path().app_config_dir().unwrap_or_else(|_| PathBuf::from("."));
        let settings_path = config_dir.join("settings.json");
        let cache_dir = app.path().app_data_dir().unwrap_or_else(|_| PathBuf::from(".")).join("published");
        let default_dir = default_data_dir(&cache_dir);
        let settings: Settings = std::fs::read_to_string(&settings_path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();
        let state = Self {
            store: RwLock::new(Arc::new(Store::new(PathBuf::new()))),
            settings: Mutex::new(settings),
            settings_path,
            default_dir,
            cache_dir,
        };
        state.reload();
        state
    }

    fn store(&self) -> Arc<Store> {
        self.store.read().unwrap().clone()
    }

    /// Folder the app reads: an explicit folder, else the cloud cache, else the local default.
    fn data_dir(&self) -> PathBuf {
        let settings = self.settings.lock().unwrap();
        match (&settings.data_dir, settings.url()) {
            (Some(dir), _) => dir.clone(),
            (None, Some(_)) => self.cache_dir.clone(),
            (None, None) => self.default_dir.clone(),
        }
    }

    /// Drop every cached table so the next request reads the folder again.
    fn reload(&self) {
        *self.store.write().unwrap() = Arc::new(Store::new(self.data_dir()));
    }

    fn save(&self, settings: Settings) -> Result<()> {
        if let Some(parent) = self.settings_path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        std::fs::write(&self.settings_path, serde_json::to_string_pretty(&settings).unwrap()).map_err(|e| e.to_string())?;
        *self.settings.lock().unwrap() = settings;
        self.reload();
        Ok(())
    }
}

/// QR_DATA_DIR if set; in debug builds the repository's data/published; otherwise the cache.
fn default_data_dir(cache_dir: &std::path::Path) -> PathBuf {
    if let Some(dir) = std::env::var_os("QR_DATA_DIR") {
        return PathBuf::from(dir);
    }
    if cfg!(debug_assertions) {
        return PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../data/published");
    }
    cache_dir.to_path_buf()
}

async fn blocking<T: Send + 'static>(store: Arc<Store>, f: impl FnOnce(&Store) -> Result<T> + Send + 'static) -> Result<T> {
    tauri::async_runtime::spawn_blocking(move || f(&store)).await.map_err(|e| e.to_string())?
}

/// A time series for the charts: days since 1970-01-01 and values.
#[derive(Debug, Default, Serialize)]
pub struct Series {
    pub t: Vec<i32>,
    pub v: Vec<f64>,
}

#[derive(Debug, Serialize)]
pub struct Overview {
    data_dir: String,
    generated_at: String,
    data_date: String,
    history_start: String,
    history_end: String,
    holdout_start: String,
    models: Vec<String>,
    features: Vec<String>,
    default_backtest: Config,
    n_tickers: usize,
}

#[tauri::command]
pub async fn overview(state: State<'_, AppState>) -> Result<Overview> {
    blocking(state.store(), |store| {
        let m = store.manifest()?;
        Ok(Overview {
            data_dir: store.dir.display().to_string(),
            generated_at: m.generated_at.clone(),
            data_date: m.data_date.clone(),
            history_start: m.history_start.clone(),
            history_end: m.history_end.clone(),
            holdout_start: m.holdout_start.clone(),
            models: m.models.clone(),
            features: m.features.clone(),
            default_backtest: m.default_backtest.clone(),
            n_tickers: store.signals()?.len(),
        })
    })
    .await
}

#[tauri::command]
pub async fn signals(state: State<'_, AppState>) -> Result<Vec<Signal>> {
    blocking(state.store(), |store| Ok(store.signals()?.as_ref().clone())).await
}

#[derive(Debug, Serialize)]
pub struct StockDetail {
    ticker: String,
    name: String,
    sector: String,
    industry: String,
    signal: Option<Signal>,
    /// Growth of 100 from compounded daily returns (derived; raw prices are not published).
    price: Series,
    market: Series,
    score_rank: Series,
    features: Vec<(String, f64)>,
    shap: Vec<(String, f64)>,
    base_value: f64,
}

#[tauri::command]
pub async fn stock(state: State<'_, AppState>, ticker: String) -> Result<StockDetail> {
    blocking(state.store(), move |store| {
        let universe = store.universe()?;
        let info = universe.iter().find(|s| s.ticker == ticker).ok_or_else(|| format!("unknown ticker {ticker}"))?;
        let signal = store.signals()?.iter().find(|s| s.ticker == ticker).cloned();

        let returns = store.returns()?;
        let market = store.market()?;
        let (mut price, mut bench) = (Series::default(), Series::default());
        if let Some(col) = returns.col(&ticker) {
            let (mut p, mut b) = (100.0, 100.0);
            for day in 0..returns.n_days() {
                let r = returns.get(day, col);
                if r.is_nan() {
                    continue;
                }
                let date = returns.dates[day];
                if price.t.is_empty() {
                    // Start both lines at 100 on the day before the first return.
                    price.t.push(date);
                    price.v.push(p);
                    bench.t.push(date);
                    bench.v.push(b);
                    continue;
                }
                p *= 1.0 + r;
                b *= 1.0 + market.get(&date).copied().unwrap_or(0.0);
                price.t.push(date);
                price.v.push(p);
                bench.t.push(date);
                bench.v.push(b);
            }
        }

        let ranks = store.rank_history()?;
        let score_rank = ranks
            .get(&ticker)
            .map(|rows| Series { t: rows.iter().map(|r| r.0).collect(), v: rows.iter().map(|r| r.1).collect() })
            .unwrap_or_default();

        let names = store.manifest()?.features.clone();
        let features = store
            .features()?
            .get(&ticker)
            .map(|values| names.iter().cloned().zip(values.iter().copied()).collect())
            .unwrap_or_default();
        let mut shap = store.shap()?.get(&ticker).cloned().unwrap_or_default();
        let base_value = shap.iter().find(|(f, _)| f == "base_value").map_or(0.0, |(_, v)| *v);
        shap.retain(|(f, _)| f != "base_value");
        shap.sort_by(|a, b| b.1.abs().total_cmp(&a.1.abs()));

        Ok(StockDetail {
            ticker: info.ticker.clone(),
            name: info.name.clone(),
            sector: info.sector.clone(),
            industry: info.industry.clone(),
            signal,
            price,
            market: bench,
            score_rank,
            features,
            shap,
            base_value,
        })
    })
    .await
}

#[derive(Debug, Deserialize)]
pub struct BacktestRequest {
    model: String,
    config: Config,
    start: Option<String>,
    end: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct YearRow {
    year: i32,
    net: f64,
    gross: f64,
    market: f64,
}

#[derive(Debug, Serialize)]
pub struct BacktestOutput {
    t: Vec<i32>,
    equity_net: Vec<f64>,
    equity_gross: Vec<f64>,
    equity_market: Vec<f64>,
    drawdown: Vec<f64>,
    long_ann: f64,
    short_ann: f64,
    cost_ann: f64,
    net: Option<Summary>,
    gross: Option<Summary>,
    market: Option<Summary>,
    yearly: Vec<YearRow>,
    rebalances: usize,
    elapsed_ms: f64,
}

fn compound(values: &[f64]) -> Vec<f64> {
    let mut equity = 1.0;
    values.iter().map(|r| {
        equity *= 1.0 + r;
        equity
    })
    .collect()
}

#[tauri::command]
pub async fn backtest(state: State<'_, AppState>, request: BacktestRequest) -> Result<BacktestOutput> {
    blocking(state.store(), move |store| {
        let started = Instant::now();
        let start = request.start.as_deref().and_then(parse_iso).unwrap_or(i32::MIN);
        let end = request.end.as_deref().and_then(parse_iso).unwrap_or(i32::MAX);
        let history = store.history(&request.model)?;
        let scores: Vec<_> = history.iter().filter(|s| s.date >= start && s.date <= end).cloned().collect();
        let returns = store.returns()?;
        let market = store.market()?;
        let result = run_backtest(&scores, &returns, &request.config);

        let t: Vec<i32> = result.daily.iter().map(|r| r.date).collect();
        let net: Vec<f64> = result.daily.iter().map(|r| r.net).collect();
        let gross: Vec<f64> = result.daily.iter().map(|r| r.gross).collect();
        let turnover: Vec<f64> = result.daily.iter().map(|r| r.turnover).collect();
        let mkt: Vec<f64> = t.iter().map(|d| market.get(d).copied().unwrap_or(0.0)).collect();
        let equity_net = compound(&net);
        let mut peak: f64 = 1.0;
        let drawdown = equity_net
            .iter()
            .map(|e| {
                peak = peak.max(*e);
                e / peak - 1.0
            })
            .collect();
        let n = result.daily.len().max(1) as f64;
        let per_year = |f: fn(&crate::backtest::DailyRow) -> f64| result.daily.iter().map(f).sum::<f64>() / n * 252.0;
        let yearly_net = yearly_returns(&t, &net);
        let yearly_gross = yearly_returns(&t, &gross);
        let yearly_market = yearly_returns(&t, &mkt);
        let yearly = yearly_net
            .iter()
            .zip(&yearly_gross)
            .zip(&yearly_market)
            .map(|(((year, net), (_, gross)), (_, market))| YearRow { year: *year, net: *net, gross: *gross, market: *market })
            .collect();
        let rebalances = {
            let mut dates: Vec<i32> = result.weights.iter().map(|w| w.signal_date).collect();
            dates.dedup();
            dates.len()
        };
        Ok(BacktestOutput {
            equity_gross: compound(&gross),
            equity_market: compound(&mkt),
            equity_net,
            drawdown,
            long_ann: per_year(|r| r.long),
            short_ann: per_year(|r| r.short),
            cost_ann: per_year(|r| r.cost),
            net: performance_summary(&net, &turnover, 252.0),
            gross: performance_summary(&gross, &turnover, 252.0),
            market: performance_summary(&mkt, &vec![0.0; mkt.len()], 252.0),
            yearly,
            rebalances,
            t,
            elapsed_ms: started.elapsed().as_secs_f64() * 1e3,
        })
    })
    .await
}

#[derive(Debug, Serialize)]
pub struct QualityOutput {
    models: Vec<serde_json::Map<String, serde_json::Value>>,
    /// Rolling one-year mean of daily IC per model.
    ic: Vec<(String, Series)>,
    importance: Vec<(String, f64)>,
}

#[tauri::command]
pub async fn quality(state: State<'_, AppState>) -> Result<QualityOutput> {
    blocking(state.store(), |store| {
        let q = store.quality()?;
        let models = store.manifest()?.models.clone();
        let window = 252;
        let ic = models
            .iter()
            .map(|model| {
                let rows: Vec<(i32, f64)> = q.ic.iter().filter(|r| &r.0 == model).map(|r| (r.1, r.2)).collect();
                let mut series = Series::default();
                let mut sum = 0.0;
                for (i, (date, v)) in rows.iter().enumerate() {
                    sum += v;
                    if i >= window {
                        sum -= rows[i - window].1;
                    }
                    if i + 1 >= window {
                        series.t.push(*date);
                        series.v.push(sum / window as f64);
                    }
                }
                (model.clone(), series)
            })
            .collect();
        Ok(QualityOutput { models: q.models.clone(), ic, importance: q.importance.clone() })
    })
    .await
}

#[derive(Debug, Serialize)]
pub struct SettingsView {
    data_dir: String,
    default_dir: String,
    custom_dir: bool,
    /// Effective cloud URL, empty when the cloud is off.
    data_url: String,
    built_in_url: Option<&'static str>,
}

#[tauri::command]
pub fn get_settings(state: State<'_, AppState>) -> SettingsView {
    let settings = state.settings.lock().unwrap().clone();
    SettingsView {
        data_dir: state.data_dir().display().to_string(),
        default_dir: state.default_dir.display().to_string(),
        custom_dir: settings.data_dir.is_some(),
        data_url: settings.url().unwrap_or_default(),
        built_in_url: BUILT_IN_URL,
    }
}

/// Read a different folder (empty = back to the cloud cache or the default folder).
#[tauri::command]
pub fn set_data_dir(state: State<'_, AppState>, dir: String) -> Result<SettingsView> {
    let dir = dir.trim();
    let mut settings = state.settings.lock().unwrap().clone();
    settings.data_dir = (!dir.is_empty()).then(|| PathBuf::from(dir));
    if let Some(path) = &settings.data_dir
        && !path.join("manifest.json").is_file()
    {
        return Err(format!("no manifest.json in {}", path.display()));
    }
    state.save(settings)?;
    Ok(get_settings(state))
}

/// Set the cloud URL ("" switches the cloud off, "default" restores the built-in URL).
#[tauri::command]
pub fn set_data_url(state: State<'_, AppState>, url: String) -> Result<SettingsView> {
    let url = url.trim();
    let mut settings = state.settings.lock().unwrap().clone();
    settings.data_url = match url {
        "default" => None,
        "" => Some(String::new()),
        url => {
            HttpSource::new(url)?;
            Some(url.trim_end_matches('/').to_string())
        }
    };
    state.save(settings)?;
    Ok(get_settings(state))
}

/// Download what changed from the cloud into the data folder, then reload it.
#[tauri::command]
pub async fn sync_data(state: State<'_, AppState>) -> Result<SyncReport> {
    let url = state.settings.lock().unwrap().url().ok_or("no data URL configured")?;
    let dir = state.data_dir();
    let report = tauri::async_runtime::spawn_blocking(move || sync(&HttpSource::new(&url)?, &dir))
        .await
        .map_err(|e| e.to_string())??;
    if report.downloaded > 0 || report.removed > 0 {
        state.reload();
    }
    Ok(report)
}
