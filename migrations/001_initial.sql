CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE study (
    id uuid PRIMARY KEY, code text NOT NULL UNIQUE, environment text NOT NULL DEFAULT 'synthetic',
    CHECK (environment = 'synthetic')
);
CREATE TABLE cohort (
    id uuid PRIMARY KEY, study_id uuid NOT NULL REFERENCES study, code text NOT NULL,
    UNIQUE (study_id, code)
);
CREATE TABLE household (id uuid PRIMARY KEY, code text NOT NULL UNIQUE);
CREATE TABLE membership (
    cohort_id uuid NOT NULL REFERENCES cohort, household_id uuid NOT NULL REFERENCES household,
    valid_from timestamptz NOT NULL, valid_to timestamptz,
    PRIMARY KEY (cohort_id, household_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE TABLE meter (id uuid PRIMARY KEY, code text NOT NULL UNIQUE);
CREATE TABLE stream (
    id uuid PRIMARY KEY, meter_id uuid NOT NULL REFERENCES meter, code text NOT NULL UNIQUE,
    kind text NOT NULL CHECK (kind IN ('pulse', 'interval', 'index')),
    liters_per_pulse numeric(24,9), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    valid_from timestamptz NOT NULL DEFAULT '2000-01-01T00:00:00Z', valid_to timestamptz,
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    EXCLUDE USING gist (meter_id WITH =, tstzrange(valid_from, valid_to, '[)') WITH &&),
    CHECK ((kind = 'pulse' AND liters_per_pulse > 0) OR
           (kind <> 'pulse' AND liters_per_pulse IS NULL))
);
CREATE TABLE installation (
    id uuid PRIMARY KEY, meter_id uuid NOT NULL REFERENCES meter,
    household_id uuid NOT NULL REFERENCES household,
    valid_from timestamptz NOT NULL, valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), retired_at timestamptz,
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (retired_at IS NULL OR retired_at >= recorded_at),
    EXCLUDE USING gist (meter_id WITH =, tstzrange(valid_from, valid_to, '[)') WITH &&)
        WHERE (retired_at IS NULL)
);

CREATE TABLE source (
    id uuid PRIMARY KEY, sha256 char(64) NOT NULL, byte_size bigint NOT NULL CHECK (byte_size >= 0),
    object_key text NOT NULL, contract_version text NOT NULL, parser_version text NOT NULL,
    state text NOT NULL CHECK (state IN ('accepted', 'processing', 'published', 'failed')),
    accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(), published_at timestamptz,
    UNIQUE (sha256, contract_version, parser_version),
    CHECK ((state = 'published') = (published_at IS NOT NULL))
);
CREATE TABLE job (
    id uuid PRIMARY KEY, source_id uuid NOT NULL UNIQUE REFERENCES source,
    state text NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed')),
    attempts integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL CHECK (max_attempts > 0),
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_until timestamptz, token uuid,
    checkpoint bigint NOT NULL DEFAULT 0, error_code text,
    CHECK (attempts >= 0 AND checkpoint >= 0),
    CHECK (state <> 'running' OR (lease_until IS NOT NULL AND token IS NOT NULL))
);
CREATE INDEX job_claim ON job (available_at) WHERE state IN ('queued', 'running');

CREATE TABLE staged_measurement (
    source_id uuid NOT NULL REFERENCES source, row_no bigint NOT NULL,
    stream_id uuid NOT NULL REFERENCES stream, event_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    kind text NOT NULL CHECK (kind IN ('pulse', 'interval', 'index')),
    occurred_at timestamptz NOT NULL, interval_start timestamptz, received_at timestamptz NOT NULL,
    original_time text NOT NULL, original_value numeric(24,9) NOT NULL CHECK (original_value >= 0),
    original_unit text NOT NULL, liters numeric(30,12) NOT NULL CHECK (liters >= 0),
    fingerprint char(64) NOT NULL,
    PRIMARY KEY (source_id, row_no)
);
CREATE TABLE measurement (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream_id uuid NOT NULL REFERENCES stream, event_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    kind text NOT NULL CHECK (kind IN ('pulse', 'interval', 'index')),
    occurred_at timestamptz NOT NULL, interval_start timestamptz,
    original_time text NOT NULL, original_value numeric(24,9) NOT NULL CHECK (original_value >= 0),
    original_unit text NOT NULL, liters numeric(30,12) NOT NULL CHECK (liters >= 0),
    fingerprint char(64) NOT NULL,
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (stream_id, event_id, revision)
);
CREATE INDEX measurement_time ON measurement (stream_id, occurred_at);
CREATE VIEW current_measurement AS
    SELECT DISTINCT ON (stream_id, event_id) * FROM measurement
    ORDER BY stream_id, event_id, revision DESC;
CREATE TABLE provenance (
    source_id uuid NOT NULL REFERENCES source, row_no bigint NOT NULL,
    measurement_id bigint NOT NULL REFERENCES measurement,
    received_at timestamptz NOT NULL, PRIMARY KEY (source_id, row_no)
);
CREATE TABLE quality_issue (
    source_id uuid NOT NULL REFERENCES source, row_no bigint NOT NULL,
    code text NOT NULL, PRIMARY KEY (source_id, row_no, code)
);
CREATE TABLE dataset (
    id uuid PRIMARY KEY, state text NOT NULL CHECK (state IN ('ready', 'invalidated')),
    object_key text NOT NULL UNIQUE, manifest jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE dataset_source (
    dataset_id uuid NOT NULL REFERENCES dataset, source_id uuid NOT NULL REFERENCES source,
    PRIMARY KEY (dataset_id, source_id)
);
CREATE TABLE audit_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action text NOT NULL, target_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
