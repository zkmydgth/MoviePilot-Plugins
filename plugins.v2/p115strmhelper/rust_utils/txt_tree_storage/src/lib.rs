use std::collections::HashSet;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::PathBuf;

use pyo3::prelude::*;
use pyo3::types::{PyIterator, PyModuleMethods};

const BUF_SIZE: usize = 1048576;

// 与 Python `open(..., "w", encoding="utf-8")` 文本模式行为一致：
// Windows 写入 CRLF，其他平台写入 LF。读取侧使用 trim() 兼容两种换行。
#[cfg(windows)]
const LINE_END: &[u8] = b"\r\n";
#[cfg(not(windows))]
const LINE_END: &[u8] = b"\n";

fn io_err(e: std::io::Error) -> PyErr {
    if e.kind() == std::io::ErrorKind::NotFound {
        return PyErr::new::<pyo3::exceptions::PyFileNotFoundError, _>(e.to_string());
    }
    PyErr::new::<pyo3::exceptions::PyOSError, _>(e.to_string())
}

fn load_other_lines_set(path: &PathBuf) -> PyResult<HashSet<String>> {
    let file = match fs::File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(HashSet::new()),
        Err(e) => return Err(io_err(e)),
    };
    let reader = BufReader::with_capacity(BUF_SIZE, file);
    let mut set = HashSet::new();
    for line in reader.lines() {
        let line = line.map_err(io_err)?;
        set.insert(line.trim().to_string());
    }
    Ok(set)
}

fn open_self_reader(path: &PathBuf) -> PyResult<BufReader<fs::File>> {
    let file = fs::File::open(path).map_err(io_err)?;
    Ok(BufReader::with_capacity(BUF_SIZE, file))
}

#[pyfunction]
#[pyo3(signature = (file_path, paths, append=false))]
fn add_paths(
    _py: Python<'_>,
    file_path: PathBuf,
    paths: &Bound<'_, PyAny>,
    append: bool,
) -> PyResult<()> {
    let iter = PyIterator::from_object(paths)?;
    let file = OpenOptions::new()
        .create(true)
        .write(true)
        .append(append)
        .truncate(!append)
        .open(&file_path)
        .map_err(io_err)?;
    let mut w = BufWriter::with_capacity(BUF_SIZE, file);
    for item in iter {
        let item = item?;
        let s: String = item.extract()?;
        w.write_all(s.as_bytes()).map_err(io_err)?;
        w.write_all(LINE_END).map_err(io_err)?;
    }
    w.flush().map_err(io_err)?;
    Ok(())
}

/// 惰性迭代器：返回 self 中存在但 other 中不存在的路径字符串。
#[pyclass]
struct CompareTreesIter {
    reader: BufReader<fs::File>,
    set: HashSet<String>,
    buf: String,
}

#[pymethods]
impl CompareTreesIter {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self) -> PyResult<Option<String>> {
        loop {
            self.buf.clear();
            let n = self.reader.read_line(&mut self.buf).map_err(io_err)?;
            if n == 0 {
                return Ok(None);
            }
            let trimmed = self.buf.trim();
            if !self.set.contains(trimmed) {
                return Ok(Some(trimmed.to_string()));
            }
        }
    }
}

/// 惰性迭代器：返回差异路径在 self 中的 1-based 行号。
#[pyclass]
struct CompareTreesLinesIter {
    reader: BufReader<fs::File>,
    set: HashSet<String>,
    buf: String,
    line_num: u64,
}

#[pymethods]
impl CompareTreesLinesIter {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self) -> PyResult<Option<u64>> {
        loop {
            self.buf.clear();
            let n = self.reader.read_line(&mut self.buf).map_err(io_err)?;
            if n == 0 {
                return Ok(None);
            }
            self.line_num += 1;
            let trimmed = self.buf.trim();
            if !self.set.contains(trimmed) {
                return Ok(Some(self.line_num));
            }
        }
    }
}

#[pyfunction]
fn compare_trees(
    _py: Python<'_>,
    self_path: PathBuf,
    other_path: PathBuf,
) -> PyResult<CompareTreesIter> {
    let set = load_other_lines_set(&other_path)?;
    let reader = open_self_reader(&self_path)?;
    Ok(CompareTreesIter {
        reader,
        set,
        buf: String::new(),
    })
}

#[pyfunction]
fn compare_trees_lines(
    _py: Python<'_>,
    self_path: PathBuf,
    other_path: PathBuf,
) -> PyResult<CompareTreesLinesIter> {
    let set = load_other_lines_set(&other_path)?;
    let reader = open_self_reader(&self_path)?;
    Ok(CompareTreesLinesIter {
        reader,
        set,
        buf: String::new(),
        line_num: 0,
    })
}

#[pyfunction]
fn get_path_by_line_number(
    _py: Python<'_>,
    file_path: PathBuf,
    line_number: i64,
) -> PyResult<Option<String>> {
    if line_number <= 0 {
        return Ok(None);
    }
    let file = match fs::File::open(&file_path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(io_err(e)),
    };
    let reader = BufReader::with_capacity(BUF_SIZE, file);
    let target = line_number as usize;
    for (i, line) in reader.lines().enumerate() {
        let line = line.map_err(io_err)?;
        if i + 1 == target {
            return Ok(Some(line.trim().to_string()));
        }
    }
    Ok(None)
}

#[pyfunction]
fn count(_py: Python<'_>, file_path: PathBuf) -> PyResult<u64> {
    let file = match fs::File::open(&file_path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(e) => return Err(io_err(e)),
    };
    let reader = BufReader::with_capacity(BUF_SIZE, file);
    let mut n: u64 = 0;
    for line in reader.lines() {
        let line = line.map_err(io_err)?;
        if !line.trim().is_empty() {
            n += 1;
        }
    }
    Ok(n)
}

#[pyfunction]
fn clear(file_path: PathBuf) -> PyResult<()> {
    if file_path.exists() {
        fs::remove_file(&file_path).map_err(io_err)?;
    }
    Ok(())
}

#[pymodule]
fn txt_tree_storage(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<CompareTreesIter>()?;
    m.add_class::<CompareTreesLinesIter>()?;
    m.add_function(wrap_pyfunction!(add_paths, m)?)?;
    m.add_function(wrap_pyfunction!(compare_trees, m)?)?;
    m.add_function(wrap_pyfunction!(compare_trees_lines, m)?)?;
    m.add_function(wrap_pyfunction!(get_path_by_line_number, m)?)?;
    m.add_function(wrap_pyfunction!(count, m)?)?;
    m.add_function(wrap_pyfunction!(clear, m)?)?;
    Ok(())
}
