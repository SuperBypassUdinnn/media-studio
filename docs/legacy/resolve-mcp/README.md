# Legacy: resolve-mcp

This package has been archived and retired as part of the Media Studio v2 architecture (Phase 4).

## Why Retired

1. **Linux External Scripting Limitation**:
   Empirical testing showed that DaVinci Resolve Free on Linux does not expose the external scripting API. `scriptapp('Resolve')` returns `None` regardless of `RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`, or `PYTHONPATH`. Every live automation tool required DaVinci Resolve Studio ($295).

2. **Codec Licensing Constraints**:
   The Free Linux build of Resolve does not decode H.264 or H.265 without manual transcode to DNxHR.

3. **Modern Permissive Replacement**:
   FilmCraft 0.15.0 and EffectCraft 0.19.0 (dual MIT/Apache-2.0) provide:
   - 711 typed headless engine commands via CLI and stdio MCP.
   - Native keyframe animation, multi-track timeline, and direct H.264/ProRes rendering.
   - Built-in BS.1770 loudness targeting (`-14.0 LUFS`).
   - Open interchange formats (OTIO, FCPXML).

4. **Audio DSP Migration**:
   All valuable standalone audio DSP capabilities (`measure_loudness`, `normalize_loudness`, `detect_beats`, `duck_music`, `remove_silence`) were migrated into `media_studio.audio`.
