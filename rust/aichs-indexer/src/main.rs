use std::env;
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};

#[derive(Clone)]
struct Entry {
    rel: String,
    name: String,
}

#[derive(Clone)]
struct Match {
    score: i32,
    rel: String,
    name: String,
    indices: Vec<usize>,
}

fn main() {
    let mut args = env::args().skip(1);
    let Some(root_arg) = args.next() else {
        emit_error("missing workspace root");
        return;
    };
    let ignored: Vec<String> = args
        .next()
        .unwrap_or_default()
        .split(',')
        .filter(|part| !part.is_empty())
        .map(str::to_owned)
        .collect();
    let root = PathBuf::from(root_arg);
    let mut entries = Vec::new();
    walk(&root, &root, &ignored, &mut entries);
    entries.sort_by(|a, b| a.rel.to_lowercase().cmp(&b.rel.to_lowercase()));
    println!("READY\t{}", entries.len());
    let _ = io::stdout().flush();

    for line in io::stdin().lock().lines().map_while(Result::ok) {
        let mut parts = line.split('\t');
        match parts.next() {
            Some("Q") => {
                let query = parts.next().and_then(decode).unwrap_or_default();
                let limit = parts
                    .next()
                    .and_then(|value| value.parse().ok())
                    .unwrap_or(80);
                for item in search(&entries, &query, limit) {
                    let encoded_indices = item
                        .indices
                        .iter()
                        .map(usize::to_string)
                        .collect::<Vec<_>>()
                        .join(",");
                    println!(
                        "M\t{}\t{}\t{}\t{}",
                        item.score,
                        encode(&item.rel),
                        encode(&item.name),
                        encoded_indices
                    );
                }
                println!("D");
                let _ = io::stdout().flush();
            }
            Some("X") => break,
            _ => emit_error("invalid command"),
        }
    }
}

fn walk(root: &Path, current: &Path, ignored: &[String], entries: &mut Vec<Entry>) {
    let Ok(read_dir) = fs::read_dir(current) else {
        return;
    };
    let mut children = read_dir.filter_map(Result::ok).collect::<Vec<_>>();
    children.sort_by_key(|entry| entry.file_name().to_string_lossy().to_lowercase());
    for child in children {
        let name = child.file_name().to_string_lossy().into_owned();
        if name.starts_with('.') || ignored.iter().any(|ignored_name| ignored_name == &name) {
            continue;
        }
        let path = child.path();
        let Ok(kind) = child.file_type() else {
            continue;
        };
        if kind.is_dir() {
            walk(root, &path, ignored, entries);
        } else if kind.is_file() {
            let Ok(rel) = path.strip_prefix(root) else {
                continue;
            };
            entries.push(Entry {
                rel: rel.to_string_lossy().replace('\\', "/"),
                name,
            });
        }
    }
}

fn search(entries: &[Entry], query: &str, limit: usize) -> Vec<Match> {
    let mut matches = entries
        .iter()
        .filter_map(|entry| score_entry(entry, query))
        .collect::<Vec<_>>();
    matches.sort_by(|a, b| {
        b.score
            .cmp(&a.score)
            .then_with(|| a.rel.to_lowercase().cmp(&b.rel.to_lowercase()))
    });
    matches.truncate(limit);
    matches
}

fn score_entry(entry: &Entry, query: &str) -> Option<Match> {
    let (name_score, indices) = fuzzy_score(&entry.name, query).unwrap_or((0, Vec::new()));
    let path_score = fuzzy_score(&entry.rel, query)
        .map(|item| item.0)
        .unwrap_or(0);
    if name_score == 0 && path_score == 0 {
        return None;
    }
    Some(Match {
        score: (name_score * 2).max(path_score),
        rel: entry.rel.clone(),
        name: entry.name.clone(),
        indices,
    })
}

fn fuzzy_score(text: &str, query: &str) -> Option<(i32, Vec<usize>)> {
    let query_chars = query
        .trim()
        .chars()
        .flat_map(char::to_lowercase)
        .collect::<Vec<_>>();
    let chars = text.chars().collect::<Vec<_>>();
    if query_chars.is_empty() {
        return Some((1, Vec::new()));
    }
    let folded = chars
        .iter()
        .flat_map(|ch| ch.to_lowercase())
        .collect::<Vec<_>>();
    if query_chars.len() > folded.len() {
        return None;
    }
    for start in 0..=folded.len() - query_chars.len() {
        if folded[start..start + query_chars.len()] == query_chars[..] {
            let indices = (start..start + query_chars.len()).collect::<Vec<_>>();
            return Some((
                3000 - (start as i32 * 4)
                    + boundary_bonus(&chars, start)
                    + query_chars.len() as i32,
                indices,
            ));
        }
    }
    let mut indices = Vec::new();
    let mut query_index = 0;
    let mut score = 0;
    for (index, ch) in folded.iter().enumerate() {
        if *ch != query_chars[query_index] {
            continue;
        }
        score += 30 + boundary_bonus(&chars, index);
        if let Some(previous) = indices.last() {
            if index == *previous + 1 {
                score += 35;
            } else {
                score -= ((index - *previous - 1) as i32).min(18);
            }
        }
        indices.push(index);
        query_index += 1;
        if query_index == query_chars.len() {
            return Some((score, indices));
        }
    }
    None
}

fn boundary_bonus(chars: &[char], index: usize) -> i32 {
    if index == 0 {
        return 55;
    }
    let previous = chars[index - 1];
    let current = chars[index];
    if "_-. /\\:".contains(previous) {
        return 45;
    }
    if previous.is_lowercase() && current.is_uppercase() {
        return 45;
    }
    if previous.is_alphabetic() != current.is_alphabetic() {
        return 25;
    }
    0
}

fn encode(text: &str) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let bytes = text.as_bytes();
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let value = ((chunk[0] as u32) << 16)
            | ((chunk.get(1).copied().unwrap_or(0) as u32) << 8)
            | chunk.get(2).copied().unwrap_or(0) as u32;
        out.push(TABLE[((value >> 18) & 63) as usize] as char);
        out.push(TABLE[((value >> 12) & 63) as usize] as char);
        out.push(if chunk.len() > 1 {
            TABLE[((value >> 6) & 63) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            TABLE[(value & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}

fn decode(text: &str) -> Option<String> {
    let mut bytes = Vec::new();
    let values = text.as_bytes().chunks(4);
    for chunk in values {
        if chunk.len() != 4 {
            return None;
        }
        let a = decode_char(chunk[0])?;
        let b = decode_char(chunk[1])?;
        let c = if chunk[2] == b'=' {
            0
        } else {
            decode_char(chunk[2])?
        };
        let d = if chunk[3] == b'=' {
            0
        } else {
            decode_char(chunk[3])?
        };
        let value = ((a as u32) << 18) | ((b as u32) << 12) | ((c as u32) << 6) | d as u32;
        bytes.push((value >> 16) as u8);
        if chunk[2] != b'=' {
            bytes.push((value >> 8) as u8);
        }
        if chunk[3] != b'=' {
            bytes.push(value as u8);
        }
    }
    String::from_utf8(bytes).ok()
}

fn decode_char(byte: u8) -> Option<u8> {
    match byte {
        b'A'..=b'Z' => Some(byte - b'A'),
        b'a'..=b'z' => Some(byte - b'a' + 26),
        b'0'..=b'9' => Some(byte - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

fn emit_error(message: &str) {
    println!("E\t{}", encode(message));
    let _ = io::stdout().flush();
}
