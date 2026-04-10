# AIUI Standalone UI

This is a standalone runtime UI (no recent projects list).
It reads:
- `../bridge/aui_ui_protocol.json`
- `../bridge/bridge_runtime.log.jsonl`

Important:
- `AUI_UI_ENTRY.json` is static integration metadata.
- Real runtime control is provided by this `ui_runner.py`.

## Run
```powershell
python ./.aui-dashboard/ui/ui_runner.py
```
This opens a local web page that shows status bar, anchors and logs.
