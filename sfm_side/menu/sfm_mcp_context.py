# sfm-mcp: runs code queued by the bridge inside SFM's script context.
# Triggered by the bridge through Qt's event loop (sfm_exec context=True);
# running it by hand from the Scripts menu does nothing.
try:
    import sfm_mcp_bridge
    sfm_mcp_bridge.run_pending()
except Exception:
    import traceback
    traceback.print_exc()
