# AICHS native terminal helper

This binary keeps terminal emulation and PTY handling out of the Qt UI. It is
intended to be bundled with AICHS in the same way as `aichs-indexer`.

The helper reads newline-delimited JSON commands from standard input and emits
newline-delimited JSON events to standard output. Binary terminal input and
output are Base64 encoded.

Commands:

```json
{"type":"start","cwd":"/workspace","program":"/bin/zsh","args":["-i"],"columns":120,"lines":30}
{"type":"input","data":"bHMNCg=="}
{"type":"resize","columns":160,"lines":42}
{"type":"shutdown"}
```

Events:

```json
{"type":"ready"}
{"type":"output","data":"Li4u"}
{"type":"frame","frame":{"columns":120,"lines":30,"text":"...","spans":[...],"cursor_row":0,"cursor_column":0}}
{"type":"exit","code":0}
```

The Qt surface owns selection, clipboard, drag/drop, terminal references, tabs,
and styling. The helper owns PTY/ConPTY lifecycle, terminal parsing, screen
state, colors, cursor movement, alternate-screen behavior, and resize state.
Frames are compact screen text with only non-default color/style spans, so a
busy terminal does not serialize a JSON cell object for every screen cell.
