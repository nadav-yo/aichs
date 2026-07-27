use alacritty_terminal::{
    event::{Event, EventListener},
    grid::Dimensions,
    index::{Column, Line},
    term::{Config, Term},
    vte::ansi::{Color, Processor, StdSyncHandler},
};
use serde::Serialize;
use std::sync::{Arc, Mutex};

#[derive(Clone, Copy)]
pub struct TerminalSize {
    pub columns: usize,
    pub lines: usize,
}

impl Dimensions for TerminalSize {
    fn columns(&self) -> usize {
        self.columns
    }

    fn screen_lines(&self) -> usize {
        self.lines
    }

    fn total_lines(&self) -> usize {
        self.lines
    }
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", content = "value", rename_all = "snake_case")]
pub enum TerminalColor {
    Named(u16),
    Indexed(u8),
    Rgb([u8; 3]),
}

impl From<Color> for TerminalColor {
    fn from(color: Color) -> Self {
        match color {
            Color::Named(value) => Self::Named(value as u16),
            Color::Indexed(value) => Self::Indexed(value),
            Color::Spec(value) => Self::Rgb([value.r, value.g, value.b]),
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct TerminalSpan {
    pub start: usize,
    pub length: usize,
    pub foreground: TerminalColor,
    pub background: TerminalColor,
    pub flags: u16,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct TerminalFrame {
    pub columns: usize,
    pub lines: usize,
    pub text: String,
    pub spans: Vec<TerminalSpan>,
    pub cursor_row: usize,
    pub cursor_column: usize,
}

/// Alacritty's terminal state machine, without a GUI renderer.
pub struct TerminalEngine {
    term: Term<TerminalListener>,
    parser: Processor<StdSyncHandler>,
    listener: TerminalListener,
}

/// Carries standard terminal replies (for example a cursor-position report)
/// back to the PTY.  Interactive shells such as PowerShell wait for these
/// before drawing their first prompt.
#[derive(Clone, Default)]
struct TerminalListener {
    pty_writes: Arc<Mutex<Vec<String>>>,
}

impl EventListener for TerminalListener {
    fn send_event(&self, event: Event) {
        if let Event::PtyWrite(text) = event {
            self.pty_writes.lock().unwrap().push(text);
        }
    }
}

impl TerminalEngine {
    pub fn new(size: TerminalSize) -> Self {
        let listener = TerminalListener::default();
        Self {
            term: Term::new(Config::default(), &size, listener.clone()),
            parser: Processor::new(),
            listener,
        }
    }

    pub fn process(&mut self, bytes: &[u8]) {
        self.parser.advance(&mut self.term, bytes);
    }

    pub fn resize(&mut self, size: TerminalSize) {
        self.term.resize(size);
    }

    pub fn take_pty_writes(&self) -> Vec<String> {
        std::mem::take(&mut *self.listener.pty_writes.lock().unwrap())
    }

    pub fn frame(&self) -> TerminalFrame {
        let columns = self.term.columns();
        let lines = self.term.screen_lines();
        let mut text = String::with_capacity(columns * lines + lines.saturating_sub(1));
        let mut spans: Vec<TerminalSpan> = Vec::new();
        for row in 0..lines {
            for column in 0..columns {
                let cell = &self.term.grid()[Line(row as i32)][Column(column)];
                let mut cell_text = cell.c.to_string();
                if let Some(zerowidth) = cell.zerowidth() {
                    cell_text.extend(zerowidth);
                }
                let foreground = cell.fg.into();
                let background = cell.bg.into();
                let flags = cell.flags.bits();
                let start = text.encode_utf16().count();
                text.push_str(&cell_text);
                let length = cell_text.encode_utf16().count();
                if !is_default_style(foreground, background, flags) && length > 0 {
                    if let Some(previous) = spans.last_mut().filter(|previous| {
                        previous.start + previous.length == start
                            && previous.foreground == foreground
                            && previous.background == background
                            && previous.flags == flags
                    }) {
                        previous.length += length;
                    } else {
                        spans.push(TerminalSpan {
                            start,
                            length,
                            foreground,
                            background,
                            flags,
                        });
                    }
                }
            }
            if row + 1 < lines {
                text.push('\n');
            }
        }
        let point = self.term.grid().cursor.point;
        TerminalFrame {
            columns,
            lines,
            text,
            spans,
            cursor_row: point.line.0.clamp(0, lines.saturating_sub(1) as i32) as usize,
            cursor_column: point.column.0.min(columns.saturating_sub(1)),
        }
    }
}

fn is_default_style(foreground: TerminalColor, background: TerminalColor, flags: u16) -> bool {
    foreground == TerminalColor::Named(256) && background == TerminalColor::Named(257) && flags == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_preserves_cursor_overwrites_and_color() {
        let mut engine = TerminalEngine::new(TerminalSize {
            columns: 8,
            lines: 2,
        });
        engine.process(b"one\r\x1b[31mRED\x1b[0m");

        let frame = engine.frame();

        assert_eq!(&frame.text[..3], "RED");
        assert_eq!(frame.spans[0].foreground, TerminalColor::Named(1));
        assert_eq!(frame.cursor_column, 3);
    }

    #[test]
    fn responds_to_standard_terminal_queries() {
        let mut engine = TerminalEngine::new(TerminalSize {
            columns: 8,
            lines: 2,
        });

        engine.process(b"\x1b[6n");

        assert_eq!(engine.take_pty_writes(), vec!["\x1b[1;1R"]);
    }
}
