# sfm-mcp: runs code queued by the bridge inside SFM's animation-set script
# context (current shot / animation set are available here, unlike in
# Scripts-menu entries). The bridge opens this menu itself; choosing the entry
# by hand does nothing.
try:
    import sfm_mcp_scene
    if not sfm_mcp_scene.run_ctx_pending():
        import sfm_mcp_bridge
        sfm_mcp_bridge.run_pending()
except Exception:
    import traceback
    traceback.print_exc()
