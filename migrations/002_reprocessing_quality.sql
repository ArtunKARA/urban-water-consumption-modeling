-- Keep published quality stable until a reprocessing attempt is atomically published.
CREATE TABLE staged_quality_issue (
    source_id uuid NOT NULL REFERENCES source, row_no bigint NOT NULL,
    code text NOT NULL, PRIMARY KEY (source_id, row_no, code)
);
