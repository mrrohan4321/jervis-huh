"""
Tiny shared slot for "who is currently recognized as JARVIS's master".
Set once at boot by face_id.identify_master() (called from server.py's
startup hook), read by llm.py (so the "who are you" reply can use the
name) and pushed to the frontend as a "master" WebSocket event so the
HUD can show who's logged in.

Deliberately just a module-level variable, not a class -- there's only
ever one master at a time for this single-user assistant.
"""
current_name = None
