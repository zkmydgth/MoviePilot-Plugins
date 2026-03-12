use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};

use ahash::AHashSet;
use rayon::prelude::*;
use rayon::ThreadPoolBuilder;
use walkdir::WalkDir;

use crate::query::parse_share_strm_line;

fn strip_bom_bytes(buf: &[u8]) -> &[u8] {
    if buf.starts_with(b"\xEF\xBB\xBF") {
        &buf[3..]
    } else {
        buf
    }
}

fn read_bounded(path: &Path, max: usize) -> std::io::Result<Vec<u8>> {
    let f = File::open(path)?;
    let mut buf = Vec::new();
    let meta = f.metadata().ok();
    let hint = meta.map(|m| (m.len() as usize).min(max)).unwrap_or(0);
    if hint > 0 {
        buf.reserve(hint.min(max));
    }
    let mut take = f.take(max as u64);
    take.read_to_end(&mut buf)?;
    Ok(buf)
}

fn is_strm_extension(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .map(|e| e.eq_ignore_ascii_case("strm"))
        .unwrap_or(false)
}

pub fn process_file_contents(bytes: &[u8]) -> AHashSet<(String, String)> {
    let mut out = AHashSet::new();
    let bytes = strip_bom_bytes(bytes);
    let s = match std::str::from_utf8(bytes) {
        Ok(s) => s,
        Err(_) => return out,
    };
    let s = s.trim();
    if s.is_empty() {
        return out;
    }
    for line in s.lines() {
        let line = line.trim_end_matches('\r').trim();
        if line.is_empty() {
            continue;
        }
        if let Some(pair) = parse_share_strm_line(line) {
            out.insert(pair);
        }
    }
    out
}

pub fn process_file(path: &Path, max_file_bytes: usize) -> AHashSet<(String, String)> {
    let buf = match read_bounded(path, max_file_bytes) {
        Ok(b) => b,
        Err(_) => return AHashSet::new(),
    };
    process_file_contents(&buf)
}

fn merge_sets(
    mut a: AHashSet<(String, String)>,
    b: AHashSet<(String, String)>,
) -> AHashSet<(String, String)> {
    if a.len() < b.len() {
        return merge_sets(b, a);
    }
    for x in b {
        a.insert(x);
    }
    a
}

pub fn collect_strm_paths(root: &Path) -> std::io::Result<Vec<PathBuf>> {
    let mut paths = Vec::new();
    for entry in WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        if !entry.file_type().is_file() {
            continue;
        }
        let p = entry.path();
        if is_strm_extension(p) {
            paths.push(p.to_path_buf());
        }
    }
    Ok(paths)
}

pub fn scan_share_strm_pairs_inner(
    root: &Path,
    max_file_bytes: usize,
    num_threads: Option<usize>,
) -> Result<Vec<(String, String)>, std::io::Error> {
    let paths = collect_strm_paths(root)?;
    let merged = if let Some(n) = num_threads {
        if n < 1 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "num_threads must be >= 1",
            ));
        }
        let pool = ThreadPoolBuilder::new()
            .num_threads(n)
            .build()
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
        pool.install(|| {
            paths
                .par_iter()
                .map(|p| process_file(p, max_file_bytes))
                .reduce(AHashSet::new, merge_sets)
        })
    } else {
        paths
            .par_iter()
            .map(|p| process_file(p, max_file_bytes))
            .reduce(AHashSet::new, merge_sets)
    };
    let mut v: Vec<_> = merged.into_iter().collect();
    v.sort();
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn process_multiline_dedup_in_file() {
        let s = b"http://x/P115StrmHelper?share_code=a&receive_code=b\nhttp://x/P115StrmHelper?share_code=a&receive_code=b\n";
        let set = process_file_contents(s);
        assert_eq!(set.len(), 1);
    }
}
