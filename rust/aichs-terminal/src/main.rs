//! Native terminal engine for AICHS.
//!
//! This helper will own the PTY and Alacritty terminal state.  It communicates
//! with the Qt process through a small newline-delimited JSON protocol so the
//! packaged app needs no browser runtime or Python terminal emulator.

mod engine;

use std::{
    io::{self, BufRead, Read, Write},
    sync::mpsc,
    thread,
    time::Duration,
};

use base64::{Engine, engine::general_purpose::STANDARD as BASE64};
use engine::{TerminalEngine, TerminalFrame, TerminalSize};
use portable_pty::{Child, CommandBuilder, MasterPty, PtySize, native_pty_system};
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum Command {
    Start {
        cwd: String,
        program: String,
        #[serde(default)]
        args: Vec<String>,
        columns: usize,
        lines: usize,
    },
    Input {
        data: String,
    },
    Resize {
        columns: usize,
        lines: usize,
    },
    Shutdown,
}

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum Event {
    Ready,
    Output { data: String },
    Frame { frame: TerminalFrame },
    Exit { code: i32 },
    Error { message: String },
}

enum PtyEvent {
    Output(Vec<u8>),
    Closed,
    Error(String),
}

struct Session {
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    child: Box<dyn Child + Send + Sync>,
}

fn main() {
    let (commands_tx, commands_rx) = mpsc::channel();
    thread::spawn(move || {
        for line in io::stdin().lock().lines() {
            let command = line.map_err(|error| error.to_string()).and_then(|line| {
                serde_json::from_str::<Command>(&line).map_err(|error| error.to_string())
            });
            if commands_tx.send(command).is_err() {
                return;
            }
        }
    });

    let mut session: Option<Session> = None;
    let mut engine: Option<TerminalEngine> = None;
    let mut pty_events: Option<mpsc::Receiver<PtyEvent>> = None;

    loop {
        match commands_rx.recv_timeout(Duration::from_millis(12)) {
            Ok(Ok(Command::Start {
                cwd,
                program,
                args,
                columns,
                lines,
            })) => {
                if session.is_some() {
                    emit(Event::Error {
                        message: "terminal is already running".into(),
                    });
                    continue;
                }
                match start_session(&cwd, &program, &args, columns, lines) {
                    Ok((new_session, receiver)) => {
                        engine = Some(TerminalEngine::new(TerminalSize { columns, lines }));
                        session = Some(new_session);
                        pty_events = Some(receiver);
                        emit(Event::Ready);
                    }
                    Err(error) => emit(Event::Error { message: error }),
                }
            }
            Ok(Ok(Command::Input { data })) => {
                if let (Some(session), Ok(bytes)) = (session.as_mut(), BASE64.decode(data)) {
                    if let Err(error) = session
                        .writer
                        .write_all(&bytes)
                        .and_then(|_| session.writer.flush())
                    {
                        emit(Event::Error {
                            message: format!("terminal input failed: {error}"),
                        });
                    }
                }
            }
            Ok(Ok(Command::Resize { columns, lines })) => {
                if let Some(session) = session.as_mut() {
                    let size = pty_size(columns, lines);
                    if let Err(error) = session.master.resize(size) {
                        emit(Event::Error {
                            message: format!("terminal resize failed: {error}"),
                        });
                    }
                    if let Some(engine) = engine.as_mut() {
                        engine.resize(TerminalSize { columns, lines });
                        emit(Event::Frame {
                            frame: engine.frame(),
                        });
                    }
                }
            }
            Ok(Ok(Command::Shutdown)) => break,
            Ok(Err(error)) => emit(Event::Error {
                message: format!("invalid terminal command: {error}"),
            }),
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }

        let Some(receiver) = pty_events.as_ref() else {
            continue;
        };
        while let Ok(event) = receiver.try_recv() {
            match event {
                PtyEvent::Output(bytes) => {
                    emit(Event::Output {
                        data: BASE64.encode(&bytes),
                    });
                    if let Some(engine) = engine.as_mut() {
                        engine.process(&bytes);
                        if let Some(session) = session.as_mut() {
                            for response in engine.take_pty_writes() {
                                if let Err(error) = session
                                    .writer
                                    .write_all(response.as_bytes())
                                    .and_then(|_| session.writer.flush())
                                {
                                    emit(Event::Error {
                                        message: format!("terminal response failed: {error}"),
                                    });
                                }
                            }
                        }
                        emit(Event::Frame {
                            frame: engine.frame(),
                        });
                    }
                }
                PtyEvent::Error(message) => emit(Event::Error { message }),
                PtyEvent::Closed => {}
            }
        }

        if let Some(active_session) = session.as_mut() {
            match active_session.child.try_wait() {
                Ok(Some(status)) => {
                    emit(Event::Exit {
                        code: status.exit_code() as i32,
                    });
                    break;
                }
                Ok(None) => {}
                Err(error) => emit(Event::Error {
                    message: format!("terminal exit check failed: {error}"),
                }),
            }
        }
    }

    if let Some(mut active_session) = session {
        let _ = active_session.child.kill();
    }
}

fn start_session(
    cwd: &str,
    program: &str,
    args: &[String],
    columns: usize,
    lines: usize,
) -> Result<(Session, mpsc::Receiver<PtyEvent>), String> {
    let pty_system = native_pty_system();
    let pair = pty_system
        .openpty(pty_size(columns, lines))
        .map_err(|error| error.to_string())?;
    let mut command = CommandBuilder::new(program);
    command.args(args);
    command.cwd(cwd);
    command.env("TERM", "xterm-256color");
    let child = pair
        .slave
        .spawn_command(command)
        .map_err(|error| error.to_string())?;
    let mut reader = pair
        .master
        .try_clone_reader()
        .map_err(|error| error.to_string())?;
    let writer = pair
        .master
        .take_writer()
        .map_err(|error| error.to_string())?;
    let (events_tx, events_rx) = mpsc::channel();
    thread::spawn(move || {
        let mut buffer = [0; 8192];
        loop {
            match reader.read(&mut buffer) {
                Ok(0) => {
                    let _ = events_tx.send(PtyEvent::Closed);
                    return;
                }
                Ok(read) => {
                    if events_tx
                        .send(PtyEvent::Output(buffer[..read].to_vec()))
                        .is_err()
                    {
                        return;
                    }
                }
                Err(error) => {
                    let _ =
                        events_tx.send(PtyEvent::Error(format!("terminal read failed: {error}")));
                    return;
                }
            }
        }
    });
    Ok((
        Session {
            master: pair.master,
            writer,
            child,
        },
        events_rx,
    ))
}

fn pty_size(columns: usize, lines: usize) -> PtySize {
    PtySize {
        rows: lines.clamp(1, u16::MAX as usize) as u16,
        cols: columns.clamp(1, u16::MAX as usize) as u16,
        pixel_width: 0,
        pixel_height: 0,
    }
}

fn emit(event: Event) {
    if let Ok(json) = serde_json::to_string(&event) {
        println!("{json}");
        let _ = io::stdout().flush();
    }
}
