# sfm-mcp: Source Filmmaker MCP server

Let Claude (or any [MCP](https://modelcontextprotocol.io) client) work in a
running **Source Filmmaker** the way Blender MCP works in Blender. It can
look at the scene, add models, lights and cameras, animate characters, take
screenshots and render final images or movies.

*Русская инструкция ниже: [Установка по-русски](#установка-по-русски).*

```
Claude / MCP client ──stdio──> sfm_mcp_server.py (Python 3.11+)
                                   │  TCP 127.0.0.1:9877 + token
                                   v
                       SFM ── sfm_mcp_bridge.py (SFM's Python 2.7, Qt timer)
```

## What it can do

| Area | Tools |
|---|---|
| Status / scripting | `sfm_status`, `sfm_launch`, `sfm_session_info`, `sfm_exec` (Python inside SFM, optionally in an animation-set script context), `sfm_api`, `sfm_console`, `sfm_reload_helpers` |
| Scene | `sfm_find_assets` (models, maps, sounds, particles, materials, sessions), `sfm_create_model`, `sfm_light` (create and set up projected lights), `sfm_camera` (create cameras, aim with `look_at`, FOV, exposure, active camera), `sfm_list_animsets`, `sfm_list_controls` |
| Transforms and keys | `sfm_get_transform`, `sfm_set_transform`, `sfm_set_control` (sliders and flexes), `sfm_get_keys`, `sfm_shift` (move a whole animation) |
| Animation | `sfm_list_sequences` and `sfm_apply_animation` (bake a model's own animations such as run, idle or taunts, with root motion), `sfm_pose` (pose bones at one frame: aim/rotate/twist in character axes), `sfm_animate` (additive keyed layer over the existing motion, eased), `sfm_joints` (check a pose numerically) |
| Viewing / output | `sfm_set_frame`, `sfm_screenshot` (viewport only, small JPEG), `sfm_render` (SFM's own exporter: PNG/JPEG/TGA sequences or MP4/MOV/AVI, single stills, with DOF, motion blur, AA and AO switches) |
| Safety | `sfm_undo` (Ctrl+Z / redo), `sfm_save_copy` (backup copy, never overwrites the open session) |

## Requirements

- Windows with **Source Filmmaker** (Steam, free).
- **Python 3.11 or newer** ([python.org](https://www.python.org/downloads/), tick
  "Add python.exe to PATH" or use the `py` launcher). This runs the MCP server
  only; SFM keeps its own Python 2.7.
- An MCP client: [Claude Code](https://claude.com/claude-code) (registered
  automatically) or Claude Desktop and others (config printed by the installer).

## Install

1. Download the release zip (or `git clone`) and unpack it somewhere permanent,
   for example `C:\Tools\sfm-mcp`. The SFM hook points to this folder.
2. Double-click **`install.bat`** (or run `py install.py`). It:
   - creates `.venv` and installs `mcp` and `pillow`;
   - adds a marked block to `SourceFilmmaker\game\usermod\scripts\sfm\sfm_init.py`
     (backup: `sfm_init.py.sfm-mcp-backup`) and copies two tiny helper scripts
     into `usermod\scripts\sfm\` (`mainmenu\sfm-mcp\` and `animset\`);
   - registers the server as `sfm` in Claude Code (user scope), or prints the
     JSON config for other clients.
3. Restart SFM, then restart your MCP client.
4. Ask Claude: *"check sfm_status"*. `bridge` should show a version.

SFM is found through Steam (all libraries). For a custom location:
`py install.py --sfm-dir "D:\SteamLibrary\steamapps\common\SourceFilmmaker\game"`.

**Claude Desktop / other clients:** add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sfm": {
      "command": "C:\\Tools\\sfm-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Tools\\sfm-mcp\\server\\sfm_mcp_server.py"]
    }
  }
}
```

**Uninstall:** `py install.py --uninstall` (removes the SFM hook, the helper
scripts and the Claude Code entry), then delete the folder.

## Things to know

- **Undo:** every edit Claude makes is one named step in SFM's Edit > Undo
  (`Claude: ...`). Ctrl+Z works as usual, and `sfm_undo` does the same.
- **Your file is never saved for you.** `sfm_save_copy` writes copies under new
  names. Rendering answers SFM's "save changes?" prompt with *Continue without
  Saving*. Press Ctrl+S yourself when you like the result.
- **Brief flashes:** creating models and `sfm_exec(context=True)` open the
  Animation Set Editor's right-click menu for a moment. SFM only allows
  `sfm.CreateModel` and similar calls from scripts started there. The shot needs
  at least one animation set for this; in an empty session, create a light or
  camera first.
- **Rendering** uses File > Export > Movie... and remembers its settings in the
  session, just like doing it by hand.
- **SFM's UI freezes** while a command runs; long renders block it as usual.
- **Network:** the bridge listens on `127.0.0.1` only and requires a random
  token (`%USERPROFILE%\.sfm_mcp\token`). Port: `SFM_MCP_PORT` (default 9877).
- **Screenshots** capture only the SFM window (Win32 PrintWindow), not your
  screen. SFM must not be minimized.

## Troubleshooting

| Problem | Fix |
|---|---|
| `sfm_status`: bridge not answering | SFM still loading, or not restarted after install. Check SFM's console for `[sfm-mcp]` lines. |
| `hook_installed: false` | Run the installer again. Use `--sfm-dir` if SFM is outside the Steam libraries. |
| Model creation: "Rig > sfm_mcp_context is missing" | Run the installer, then in SFM: Scripts > Rescan Script Menus (or restart SFM). |
| Port in use | Set `SFM_MCP_PORT` for both SFM and the MCP server. |

## Development

- `sfm_side/sfm_mcp_bridge.py`: TCP bridge inside SFM. Changes need an SFM restart.
- `sfm_side/sfm_mcp_scene.py` and `sfm_mcp_helpers.py`: scene logic.
  Hot-reload with `sfm_reload_helpers`.
- `server/sfm_mcp_server.py`: MCP tools. `server/wincapture.py`: screenshots.
- `tests/e2e_client.py`: `.venv\Scripts\python.exe tests\e2e_client.py sfm_status '{}'`
  calls one tool through a real MCP client. Without arguments it runs a smoke test.
- `tests/fake_sfm_qt.py`: offline bridge test using SFM's Python and Qt.

Hard-won SFM scripting facts are in the code comments. For example,
`log.GetValue()` returns a reused buffer, `SetKey` deletes later keys,
blend sequences need their blend table redirected, and menu actions must
be queued and never triggered synchronously.

---

## Установка по-русски

1. Нужны Windows, Source Filmmaker и **Python 3.11+** (python.org, при установке
   отметьте «Add python.exe to PATH»).
2. Распакуйте архив в постоянную папку, например `C:\Tools\sfm-mcp`.
3. Запустите **`install.bat`**. Он создаст окружение, подключит мост к SFM и
   зарегистрирует сервер `sfm` в Claude Code. Для других клиентов он покажет
   JSON-конфиг.
4. Перезапустите SFM и Claude Code и попросите Claude: «проверь sfm_status».

Если SFM стоит не в библиотеке Steam:
`py install.py --sfm-dir "D:\...\SourceFilmmaker\game"`.
Удаление: `py install.py --uninstall`.

Что важно знать:
- каждое действие Claude — отдельный шаг отмены, Ctrl+Z работает;
- ваш файл сессии не сохраняется и не перезаписывается, копии сохраняются
  под новыми именами;
- при создании моделей на долю секунды мелькает контекстное меню, так и
  задумано;
- мост слушает только `127.0.0.1` и требует токен.

## License

MIT, see [LICENSE](LICENSE). Not affiliated with Valve. Source Filmmaker is a
trademark of Valve Corporation.
