//! Mirror of the published file set: download manifest.json, fetch only files whose hash
//! changed, verify every download, and write the manifest last so an interrupted sync leaves
//! the previous snapshot readable. Without a network the app keeps using the cached snapshot.

use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::data::Result;

const MANIFEST: &str = "manifest.json";
/// Downloads are staged here and moved into place once verified.
const STAGING: &str = ".staging";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileEntry {
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Deserialize)]
struct RemoteManifest {
    data_date: String,
    files: BTreeMap<String, FileEntry>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SyncReport {
    pub data_date: String,
    pub downloaded: usize,
    pub downloaded_bytes: u64,
    pub removed: usize,
    pub files: usize,
}

/// Where published files come from: the CloudFront URL in the app, a folder in tests.
pub trait Source {
    fn get(&self, key: &str) -> Result<Vec<u8>>;
}

pub struct HttpSource {
    base: String,
    client: reqwest::blocking::Client,
}

impl HttpSource {
    pub fn new(base_url: &str) -> Result<Self> {
        let base = base_url.trim().trim_end_matches('/').to_string();
        if !base.starts_with("https://") && !base.starts_with("http://localhost") && !base.starts_with("http://127.0.0.1") {
            return Err(format!("data URL must use https: {base}"));
        }
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(120))
            .connect_timeout(Duration::from_secs(10))
            .user_agent(concat!("quant-ranker-app/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|e| e.to_string())?;
        Ok(Self { base, client })
    }
}

impl Source for HttpSource {
    fn get(&self, key: &str) -> Result<Vec<u8>> {
        let url = format!("{}/{key}", self.base);
        let response = self.client.get(&url).send().map_err(|e| format!("{url}: {e}"))?;
        if !response.status().is_success() {
            return Err(format!("{url}: HTTP {}", response.status()));
        }
        let mut body = Vec::new();
        response.take(1 << 30).read_to_end(&mut body).map_err(|e| format!("{url}: {e}"))?;
        Ok(body)
    }
}

pub fn sha256_hex(data: &[u8]) -> String {
    Sha256::digest(data).iter().map(|b| format!("{b:02x}")).collect()
}

/// Manifest keys come from the network: accept only plain relative paths inside the folder.
fn safe_key(key: &str) -> bool {
    !key.is_empty()
        && !key.starts_with('/')
        && key.split('/').all(|part| !part.is_empty() && part != "." && part != "..")
        && key.chars().all(|c| c.is_ascii_alphanumeric() || "_-./".contains(c))
}

fn local_matches(path: &Path, entry: &FileEntry) -> bool {
    match fs::metadata(path) {
        Ok(meta) if meta.len() == entry.bytes => fs::read(path).map(|data| sha256_hex(&data) == entry.sha256).unwrap_or(false),
        _ => false,
    }
}

fn write_atomic(dir: &Path, key: &str, data: &[u8]) -> Result<()> {
    let staged = dir.join(STAGING).join(key);
    let target = dir.join(key);
    for path in [&staged, &target] {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
        }
    }
    fs::write(&staged, data).map_err(|e| format!("{}: {e}", staged.display()))?;
    fs::rename(&staged, &target).map_err(|e| format!("{}: {e}", target.display()))
}

fn files_under(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(current) = stack.pop() {
        let Ok(entries) = fs::read_dir(&current) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else {
                out.push(path);
            }
        }
    }
    out
}

/// Bring `dir` up to date with the source. Files no longer listed are removed.
pub fn sync(source: &dyn Source, dir: &Path) -> Result<SyncReport> {
    let manifest_bytes = source.get(MANIFEST)?;
    let manifest: RemoteManifest = serde_json::from_slice(&manifest_bytes).map_err(|e| format!("manifest.json: {e}"))?;
    if let Some(bad) = manifest.files.keys().find(|k| !safe_key(k)) {
        return Err(format!("manifest lists an unsafe path: {bad:?}"));
    }
    fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;

    let (mut downloaded, mut downloaded_bytes) = (0, 0);
    for (key, entry) in &manifest.files {
        if local_matches(&dir.join(key), entry) {
            continue;
        }
        let data = source.get(key)?;
        if sha256_hex(&data) != entry.sha256 {
            // A cache can briefly serve an old copy; the next sync picks up the right one.
            return Err(format!("{key}: hash mismatch, try again in a few minutes"));
        }
        write_atomic(dir, key, &data)?;
        downloaded += 1;
        downloaded_bytes += data.len() as u64;
    }

    let mut removed = 0;
    for path in files_under(dir) {
        let rel = path.strip_prefix(dir).unwrap().to_string_lossy().replace('\\', "/");
        if rel != MANIFEST && !rel.starts_with(STAGING) && !manifest.files.contains_key(&rel) && fs::remove_file(&path).is_ok() {
            removed += 1;
        }
    }
    write_atomic(dir, MANIFEST, &manifest_bytes)?;
    let _ = fs::remove_dir_all(dir.join(STAGING));
    Ok(SyncReport { data_date: manifest.data_date, downloaded, downloaded_bytes, removed, files: manifest.files.len() })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    /// A folder standing in for CloudFront, counting requests.
    struct DirSource {
        dir: PathBuf,
        requests: RefCell<Vec<String>>,
    }

    impl Source for DirSource {
        fn get(&self, key: &str) -> Result<Vec<u8>> {
            self.requests.borrow_mut().push(key.to_string());
            fs::read(self.dir.join(key)).map_err(|e| e.to_string())
        }
    }

    fn publish(dir: &Path, files: &[(&str, &[u8])], data_date: &str) {
        let mut entries = serde_json::Map::new();
        for (key, data) in files {
            let path = dir.join(key);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, data).unwrap();
            entries.insert(key.to_string(), serde_json::json!({"sha256": sha256_hex(data), "bytes": data.len()}));
        }
        let manifest = serde_json::json!({"data_date": data_date, "files": entries});
        fs::write(dir.join(MANIFEST), serde_json::to_vec(&manifest).unwrap()).unwrap();
    }

    #[test]
    fn downloads_only_what_changed_and_removes_stale_files() {
        let (remote, local) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
        let source = DirSource { dir: remote.path().to_path_buf(), requests: RefCell::new(Vec::new()) };

        publish(remote.path(), &[("a.parquet", b"one"), ("returns/daily/2025.parquet", b"old year")], "2025-12-31");
        let first = sync(&source, local.path()).unwrap();
        assert_eq!((first.downloaded, first.removed, first.files), (2, 0, 2));
        assert_eq!(fs::read(local.path().join("returns/daily/2025.parquet")).unwrap(), b"old year");

        fs::remove_file(remote.path().join("a.parquet")).unwrap();
        publish(remote.path(), &[("returns/daily/2025.parquet", b"old year"), ("returns/daily/2026.parquet", b"new")], "2026-01-02");
        source.requests.borrow_mut().clear();
        let second = sync(&source, local.path()).unwrap();
        assert_eq!((second.downloaded, second.removed), (1, 1));
        assert_eq!(*source.requests.borrow(), vec!["manifest.json", "returns/daily/2026.parquet"]);
        assert!(!local.path().join("a.parquet").exists());
        assert_eq!(second.data_date, "2026-01-02");

        source.requests.borrow_mut().clear();
        let third = sync(&source, local.path()).unwrap();
        assert_eq!(third.downloaded, 0);
        assert_eq!(source.requests.borrow().len(), 1);
    }

    #[test]
    fn corrupt_download_keeps_the_old_snapshot() {
        let (remote, local) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
        let source = DirSource { dir: remote.path().to_path_buf(), requests: RefCell::new(Vec::new()) };
        publish(remote.path(), &[("a.parquet", b"good")], "2026-01-01");
        sync(&source, local.path()).unwrap();
        let old_manifest = fs::read(local.path().join(MANIFEST)).unwrap();

        publish(remote.path(), &[("a.parquet", b"better")], "2026-01-02");
        fs::write(remote.path().join("a.parquet"), b"truncated").unwrap();
        assert!(sync(&source, local.path()).unwrap_err().contains("hash mismatch"));
        assert_eq!(fs::read(local.path().join("a.parquet")).unwrap(), b"good");
        assert_eq!(fs::read(local.path().join(MANIFEST)).unwrap(), old_manifest);
    }

    #[test]
    fn rejects_paths_outside_the_folder() {
        for bad in ["../x", "/etc/passwd", "a/../../b", "a//b", "C:\\x", ""] {
            assert!(!safe_key(bad), "{bad}");
        }
        assert!(safe_key("signals/history/lgbm.parquet"));
    }
}

#[cfg(test)]
mod http_tests {
    //! Against a real server, run on demand:
    //!   QR_TEST_URL=https://….cloudfront.net cargo test http_sync -- --ignored --nocapture
    use super::*;

    #[test]
    #[ignore = "needs QR_TEST_URL pointing at a published folder"]
    fn http_sync_downloads_then_finds_nothing_new() {
        let url = std::env::var("QR_TEST_URL").expect("set QR_TEST_URL");
        let dir = tempfile::tempdir().unwrap();
        let source = HttpSource::new(&url).unwrap();
        let started = std::time::Instant::now();
        let first = sync(&source, dir.path()).unwrap();
        println!("first sync: {first:?} in {:?}", started.elapsed());
        assert_eq!(first.downloaded, first.files);
        let second = sync(&source, dir.path()).unwrap();
        println!("second sync: {second:?}");
        assert_eq!(second.downloaded, 0);
        let store = crate::data::Store::new(dir.path().to_path_buf());
        assert!(store.returns().unwrap().n_days() > 1000);
        assert!(!store.signals().unwrap().is_empty());
    }
}
