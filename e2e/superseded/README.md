# Superseded in Milestone 3

These two scripts exercised the OLD conflict API (query-string evidence, accept_a/accept_b, no field-version check) and the old prompt()-based UI.
They are kept only as a record of the Milestone 2C findings. Use instead:
- `phase_m3_conflict_ui.mjs` + `m3_setup.py` (browser, disposable database)
- `backend/tests/test_conflict_engine.py` and `backend/tests/test_atlas_integration.py`
