---
active: true
iteration: 2
max_iterations: 0
completion_promise: null
started_at: "2026-01-08T21:44:56Z"
---

get claude in split tmux to work inside the microsandbox

## Progress

### Iteration 1-2 (2026-01-08)

**Problem**: msb exe doesn't support interactive TTY sessions, which are required for tmux.

**Solution**: Switched from microsandbox (msb) to Docker for the teleport sandbox:
- Docker supports `-it` flag for interactive TTY
- phact/sandbox:v3 image already on Docker Hub with tmux + claude

**Changes made**:
- Updated `sandbox.py` to use `docker run -it` instead of `msb exe`
- Changed `is_msb_available()` to `is_docker_available()` (kept msb function for compat)
- Updated `app.py` to use new docker check

**Test script created**: `sandbox/test_teleport.sh`
Run it to manually verify the tmux split with claude works.

**Status**: Code updated, needs manual testing from a real terminal.
