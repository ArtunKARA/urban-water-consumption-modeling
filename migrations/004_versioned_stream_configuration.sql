CREATE TABLE stream_configuration (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id uuid NOT NULL REFERENCES stream,
    version integer NOT NULL CHECK (version > 0),
    kind text NOT NULL CHECK (kind IN ('pulse','interval','index')),
    liters_per_pulse numeric(24,9), valid_from timestamptz NOT NULL, valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), retired_at timestamptz,
    UNIQUE (stream_id,version), UNIQUE (id,stream_id,kind),
    FOREIGN KEY (stream_id,kind) REFERENCES stream(id,kind),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (retired_at IS NULL OR retired_at >= recorded_at),
    CHECK ((kind='pulse' AND liters_per_pulse IS NOT NULL AND liters_per_pulse>0)
        OR (kind<>'pulse' AND liters_per_pulse IS NULL)),
    EXCLUDE USING gist (stream_id WITH =, tstzrange(valid_from,valid_to,'[)') WITH &&)
        WHERE (retired_at IS NULL)
);
INSERT INTO stream_configuration(stream_id,version,kind,liters_per_pulse,valid_from,valid_to)
    SELECT id,1,kind,liters_per_pulse,valid_from,valid_to FROM stream;

ALTER TABLE measurement ADD COLUMN configuration_id uuid;
ALTER TABLE staged_measurement ADD COLUMN configuration_id uuid;
UPDATE measurement m SET configuration_id=c.id FROM stream_configuration c
    WHERE c.stream_id=m.stream_id;
UPDATE staged_measurement m SET configuration_id=c.id FROM stream_configuration c
    WHERE c.stream_id=m.stream_id;
-- Refuse to invent a configuration history for already inconsistent normalization.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM (
        SELECT configuration_id,kind,original_value,original_unit,liters FROM measurement
        UNION ALL SELECT configuration_id,kind,original_value,original_unit,liters
            FROM staged_measurement) m JOIN stream_configuration c ON c.id=m.configuration_id
        WHERE m.liters <> m.original_value * CASE WHEN m.kind='pulse' THEN c.liters_per_pulse
            WHEN m.original_unit='m3' THEN 1000 ELSE 1 END)
    THEN RAISE EXCEPTION 'Existing normalization does not match its legacy configuration'; END IF;
END $$;
ALTER TABLE measurement ALTER COLUMN configuration_id SET NOT NULL;
ALTER TABLE staged_measurement ALTER COLUMN configuration_id SET NOT NULL;
ALTER TABLE measurement ADD FOREIGN KEY (configuration_id,stream_id,kind)
    REFERENCES stream_configuration(id,stream_id,kind);
ALTER TABLE staged_measurement ADD FOREIGN KEY (configuration_id,stream_id,kind)
    REFERENCES stream_configuration(id,stream_id,kind);
CREATE OR REPLACE VIEW current_measurement AS
    SELECT DISTINCT ON (stream_id,event_id) * FROM measurement
    ORDER BY stream_id,event_id,revision DESC;

ALTER TABLE job ADD COLUMN configurations_pinned boolean NOT NULL DEFAULT false;
CREATE TABLE job_configuration (
    job_id uuid NOT NULL REFERENCES job, configuration_id uuid NOT NULL REFERENCES stream_configuration,
    PRIMARY KEY (job_id,configuration_id)
);
INSERT INTO job_configuration SELECT j.id,c.id FROM job j CROSS JOIN stream_configuration c
    WHERE j.checkpoint>0 OR j.state='running';
UPDATE job SET configurations_pinned=true WHERE checkpoint>0 OR state='running';

CREATE FUNCTION guard_configuration() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Configuration history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF (to_jsonb(NEW)-'retired_at') IS DISTINCT FROM (to_jsonb(OLD)-'retired_at')
       OR OLD.retired_at IS NOT NULL OR NEW.retired_at IS NULL THEN
        RAISE EXCEPTION 'Configuration payload is immutable; append a version'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER configuration_immutable BEFORE UPDATE OR DELETE ON stream_configuration
    FOR EACH ROW EXECUTE FUNCTION guard_configuration();

CREATE FUNCTION guard_legacy_stream() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.id,NEW.meter_id,NEW.code,NEW.kind,NEW.liters_per_pulse,NEW.valid_from,NEW.valid_to)
        IS DISTINCT FROM
       ROW(OLD.id,OLD.meter_id,OLD.code,OLD.kind,OLD.liters_per_pulse,OLD.valid_from,OLD.valid_to)
    THEN RAISE EXCEPTION 'Stream identity and legacy config are immutable; append configuration'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER legacy_stream_immutable BEFORE UPDATE ON stream
    FOR EACH ROW EXECUTE FUNCTION guard_legacy_stream();
CREATE FUNCTION initial_stream_configuration() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO stream_configuration(stream_id,version,kind,liters_per_pulse,valid_from,valid_to)
        VALUES (NEW.id,1,NEW.kind,NEW.liters_per_pulse,NEW.valid_from,NEW.valid_to);
    RETURN NEW;
END $$;
CREATE TRIGGER stream_initial_config AFTER INSERT ON stream
    FOR EACH ROW EXECUTE FUNCTION initial_stream_configuration();

CREATE FUNCTION guard_normalization() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c stream_configuration%ROWTYPE;
BEGIN
    SELECT * INTO STRICT c FROM stream_configuration WHERE id=NEW.configuration_id;
    IF NEW.liters <> NEW.original_value * (CASE WHEN NEW.kind='pulse' THEN c.liters_per_pulse
        WHEN NEW.original_unit='m3' THEN 1000 ELSE 1 END) THEN
        RAISE EXCEPTION 'Volume does not match pinned configuration' USING ERRCODE='23514';
    END IF;
    IF (NEW.kind='interval' AND (NEW.interval_start<c.valid_from OR
        (c.valid_to IS NOT NULL AND NEW.occurred_at>c.valid_to))) OR
       (NEW.kind<>'interval' AND (NEW.occurred_at<c.valid_from OR
        (c.valid_to IS NOT NULL AND (NEW.occurred_at>c.valid_to OR
            (NEW.kind='pulse' AND NEW.occurred_at=c.valid_to))))) THEN
        RAISE EXCEPTION 'Measurement outside configuration validity' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER measurement_normalization BEFORE INSERT OR UPDATE ON measurement
    FOR EACH ROW EXECUTE FUNCTION guard_normalization();
CREATE TRIGGER staging_normalization BEFORE INSERT OR UPDATE ON staged_measurement
    FOR EACH ROW EXECUTE FUNCTION guard_normalization();
