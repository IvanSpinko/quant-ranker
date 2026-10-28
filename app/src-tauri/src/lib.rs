//! Quant Ranker desktop app: reads published files and runs the backtest locally.
//! It never computes features or predictions; those come from the Python core.

pub mod backtest;
pub mod commands;
pub mod data;
pub mod dates;

use tauri::Manager;

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let state = commands::AppState::new(app.handle());
            app.manage(state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::overview,
            commands::signals,
            commands::stock,
            commands::backtest,
            commands::quality,
            commands::get_settings,
            commands::set_data_dir,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Quant Ranker app");
}
