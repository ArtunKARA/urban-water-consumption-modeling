-- SQL CHECK must explicitly reject NULL pulse scale.
ALTER TABLE stream ADD CONSTRAINT pulse_scale_required CHECK (
    kind <> 'pulse' OR (liters_per_pulse IS NOT NULL AND liters_per_pulse > 0)
);
ALTER TABLE stream ADD CONSTRAINT stream_identity_kind UNIQUE (id,kind);
ALTER TABLE membership ADD CONSTRAINT membership_no_overlap EXCLUDE USING gist (
    cohort_id WITH =, household_id WITH =, tstzrange(valid_from,valid_to,'[)') WITH &&
);
ALTER TABLE measurement ADD CONSTRAINT measurement_stream_kind
    FOREIGN KEY (stream_id,kind) REFERENCES stream(id,kind);
ALTER TABLE staged_measurement ADD CONSTRAINT staging_stream_kind
    FOREIGN KEY (stream_id,kind) REFERENCES stream(id,kind);
ALTER TABLE measurement ADD CONSTRAINT measurement_semantics CHECK (
    ((kind='interval' AND interval_start IS NOT NULL AND interval_start < occurred_at)
      OR (kind<>'interval' AND interval_start IS NULL))
    AND ((kind='pulse' AND original_unit='pulse' AND original_value=trunc(original_value))
      OR (kind<>'pulse' AND original_unit IN ('L','m3')))
);
ALTER TABLE staged_measurement ADD CONSTRAINT staging_semantics CHECK (
    ((kind='interval' AND interval_start IS NOT NULL AND interval_start < occurred_at)
      OR (kind<>'interval' AND interval_start IS NULL))
    AND ((kind='pulse' AND original_unit='pulse' AND original_value=trunc(original_value))
      OR (kind<>'pulse' AND original_unit IN ('L','m3')))
);
