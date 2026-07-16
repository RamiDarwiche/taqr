_MODELS_TABLE__DDL = """
CREATE TABLE provenance.models (
    model_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name VARCHAR(255) NOT NULL UNIQUE
)
"""

_RUNS_TABLE__DDL = """
CREATE TABLE provenance.runs (
    run_id uuid PRIMARY KEY,
    session_id uuid NOT NULL,
    model_id uuid NOT NULL REFERENCES provenance.models(model_id),
    status VARCHAR(50) NOT NULL DEFAULT 'running',
    error TEXT DEFAULT NULL,
    start_ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_ts TIMESTAMP
)
"""

_EVENTS_TABLE__DDL = """
CREATE TABLE provenance.events (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL,
    run_id uuid NOT NULL REFERENCES provenance.runs(run_id),
    event_type VARCHAR(50) NOT NULL,
    ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
)
"""

_PROVENANCE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS provenance"
