# Vendor Onboarding — developer targets
# Stack: FastAPI + Streamlit + SQLite + Gemini 2.5 Flash + local files.
#
# Cross-platform note: file deletion in `demo-reset` is done via a Python
# one-liner (not Unix `rm`) so the target works on Windows, macOS, and Linux.

.PHONY: help install run-backend run-frontend seed test demo-reset

help:
	@echo "Targets:"
	@echo "  install       pip install -r requirements.txt"
	@echo "  run-backend   start FastAPI on :8000 (uvicorn --reload)"
	@echo "  run-frontend  start Streamlit on :8501"
	@echo "  seed          seed prior vendors for the duplicate/fraud edge cases"
	@echo "  test          run the full pytest suite"
	@echo "  demo-reset    wipe the SQLite db, re-seed, and re-generate fixtures"

install:
	pip install -r requirements.txt

run-backend:
	uvicorn backend.main:app --reload --port 8000

run-frontend:
	streamlit run frontend/app.py --server.port 8501

seed:
	python -m database.seed

test:
	pytest

# Wipe the SQLite db, re-seed prior vendors, and re-generate edge-case fixtures.
# Idempotent and safe to run repeatedly: the db delete is a no-op when absent,
# seeding inserts-if-absent / updates-in-place, and fixtures overwrite in place.
demo-reset:
	python -c "import os; p='database/app.db'; os.remove(p) if os.path.exists(p) else None; print('db wiped:', p)"
	python -m database.seed
	python -m fixtures.generate_fixtures
