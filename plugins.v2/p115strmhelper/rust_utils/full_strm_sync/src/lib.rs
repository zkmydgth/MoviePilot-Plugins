mod processor;
mod types;

use pyo3::prelude::*;
use rayon::prelude::*;
use processor::Processor;
use types::*;

#[pyfunction]
fn process_batch(py: Python, config_json: String, batch: Vec<FileInput>) -> PyResult<PackedResult> {
    let config: Config = serde_json::from_str(&config_json)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("解析 JSON 失败: {}", e)))?;

    let processor = Processor::new(config);

    let results: Vec<ProcessingResult> = py.allow_threads(|| {
        batch.par_iter()
            .map(|item| processor.process_item(item))
            .collect()
    });

    let mut strm_results = Vec::new();
    let mut download_results = Vec::new();
    let mut skip_results = Vec::new();
    let mut fail_results = Vec::new();

    for res in results {
        match res {
            ProcessingResult::Strm(info) => strm_results.push(info),
            ProcessingResult::Download(info) => download_results.push(info),
            ProcessingResult::Skip(info) => skip_results.push(info),
            ProcessingResult::Fail(info) => fail_results.push(info),
        }
    }

    Ok(PackedResult { strm_results, download_results, skip_results, fail_results })
}

#[pymodule]
fn full_strm_sync(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(process_batch, m)?)?;
    m.add_class::<StrmInfo>()?;
    m.add_class::<DownloadInfo>()?;
    m.add_class::<SkipInfo>()?;
    m.add_class::<FailInfo>()?;
    m.add_class::<PackedResult>()?;
    Ok(())
}
