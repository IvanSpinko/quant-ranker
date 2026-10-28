//! Golden test: the Rust backtest must reproduce the Python reference on the same inputs.
//! Regenerate the fixture with `uv run python tests/make_golden.py` from the repository root.

use quant_ranker_lib::backtest::{Config, Returns, Score, performance_summary, run_backtest, yearly_returns};
use quant_ranker_lib::dates::parse_iso;
use serde_json::Value;

const TOLERANCE: f64 = 1e-10;

fn fixture() -> Value {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/golden/backtest.json");
    serde_json::from_str(&std::fs::read_to_string(path).expect("golden fixture")).unwrap()
}

fn date(v: &Value) -> i32 {
    parse_iso(v.as_str().unwrap()).unwrap()
}

fn num(v: &Value) -> f64 {
    v.as_f64().unwrap_or(f64::NAN)
}

fn close(actual: f64, expected: f64, what: &str) {
    let ok = (actual.is_nan() && expected.is_nan()) || (actual - expected).abs() <= TOLERANCE * (1.0 + expected.abs());
    assert!(ok, "{what}: rust {actual} vs python {expected}");
}

#[test]
fn backtest_matches_python_reference() {
    let data = fixture();
    let returns = Returns::from_rows(
        data["returns"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| (date(&r["date"]), r["ticker"].as_str().unwrap().to_string(), num(&r["ret"]))),
    );
    let scores: Vec<Score> = data["scores"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| Score { date: date(&r["date"]), ticker: r["ticker"].as_str().unwrap().to_string(), score: num(&r["score"]) })
        .collect();

    for case in data["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let config: Config = serde_json::from_value(case["config"].clone()).unwrap();
        let result = run_backtest(&scores, &returns, &config);

        let weights = case["weights"].as_array().unwrap();
        assert_eq!(result.weights.len(), weights.len(), "{name}: number of weights");
        for (w, e) in result.weights.iter().zip(weights) {
            assert_eq!(w.signal_date, date(&e["signal_date"]), "{name}: weight date");
            assert_eq!(w.ticker, e["ticker"].as_str().unwrap(), "{name}: weight ticker");
            close(w.weight, num(&e["weight"]), &format!("{name}: weight {}", w.ticker));
        }

        let daily = case["daily"].as_array().unwrap();
        assert_eq!(result.daily.len(), daily.len(), "{name}: number of days");
        for (row, e) in result.daily.iter().zip(daily) {
            assert_eq!(row.date, date(&e["date"]), "{name}: daily date");
            for (field, value) in [
                ("gross", row.gross),
                ("cost", row.cost),
                ("net", row.net),
                ("turnover", row.turnover),
                ("long", row.long),
                ("short", row.short),
            ] {
                close(value, num(&e[field]), &format!("{name}: {field} on {}", e["date"]));
            }
        }

        let net: Vec<f64> = result.daily.iter().map(|r| r.net).collect();
        let turnover: Vec<f64> = result.daily.iter().map(|r| r.turnover).collect();
        let summary = serde_json::to_value(performance_summary(&net, &turnover, 252.0).unwrap()).unwrap();
        for (key, expected) in case["summary"].as_object().unwrap() {
            close(num(&summary[key]), num(expected), &format!("{name}: summary {key}"));
        }

        let dates: Vec<i32> = result.daily.iter().map(|r| r.date).collect();
        let yearly = yearly_returns(&dates, &net);
        let expected = case["yearly"].as_array().unwrap();
        assert_eq!(yearly.len(), expected.len(), "{name}: years");
        for ((year, ret), e) in yearly.iter().zip(expected) {
            assert_eq!(*year as i64, e["year"].as_i64().unwrap());
            close(*ret, num(&e["ret"]), &format!("{name}: year {year}"));
        }
    }
}
