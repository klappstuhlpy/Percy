//! External malware scan helper.
//!
//! Calls the klappstuhl.me `/api/scan` endpoint which runs ClamAV + VirusTotal
//! against uploaded files. The dashboard has no local scanner; it forwards each
//! file to this service using an `images:read` bearer token.

use serde::Deserialize;

/// Report returned by the external scan service.
#[allow(dead_code)] // fields are part of the API contract; not all consumed yet
#[derive(Debug, Clone, Deserialize)]
pub struct ScanReport {
    pub verdict: String,       // "clean", "infected", or "unknown"
    #[serde(default)]
    pub clamav_virus: Option<String>,
    #[serde(default)]
    pub vt_status: Option<String>,
    pub file_size: i64,
    pub sha256: String,
}

/// Scan an arbitrary file via the klappstuhl.me scan endpoint.
///
/// `token` is a klappstuhl.me API token with `images:read` scope.
/// `endpoint` defaults to `https://klappstuhl.me/api/scan`.
pub async fn scan_file(data: &[u8], token: &str, endpoint: &str) -> Result<ScanReport, anyhow::Error> {
    let form = reqwest::multipart::Form::new()
        .part("file", reqwest::multipart::Part::bytes(data.to_vec()));

    let resp = reqwest::Client::new()
        .post(endpoint)
        .header(reqwest::header::AUTHORIZATION, format!("Bearer {token}"))
        .multipart(form)
        .send()
        .await?;

    Ok(resp.json::<ScanReport>().await?)
}
