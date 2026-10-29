-- Existing objects/jobs keep the legacy namespace and exact parser behavior.
ALTER TABLE source ADD COLUMN namespace text NOT NULL DEFAULT 'legacy';
ALTER TABLE source ADD COLUMN normalizer_options jsonb NOT NULL DEFAULT '{}';
ALTER TABLE source ADD COLUMN options_sha256 char(64) NOT NULL
    DEFAULT '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a';
ALTER TABLE source ADD COLUMN acquisition jsonb NOT NULL
    DEFAULT '{"reader_version":"local-file/1","data_kind":"synthetic"}';
ALTER TABLE source DROP CONSTRAINT source_sha256_contract_version_parser_version_key;
ALTER TABLE source ADD CONSTRAINT source_adapter_identity
    UNIQUE (sha256,contract_version,parser_version,namespace,options_sha256);
ALTER TABLE source ADD CHECK (namespace ~ '^[A-Za-z0-9_-]{1,64}$');
ALTER TABLE source ADD CHECK (jsonb_typeof(normalizer_options)='object');
ALTER TABLE source ADD CHECK (jsonb_typeof(acquisition)='object');

CREATE FUNCTION guard_source_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.id,NEW.sha256,NEW.byte_size,NEW.object_key,NEW.contract_version,
           NEW.parser_version,NEW.namespace,NEW.normalizer_options,NEW.options_sha256,
           NEW.acquisition,NEW.accepted_at)
       IS DISTINCT FROM
       ROW(OLD.id,OLD.sha256,OLD.byte_size,OLD.object_key,OLD.contract_version,
           OLD.parser_version,OLD.namespace,OLD.normalizer_options,OLD.options_sha256,
           OLD.acquisition,OLD.accepted_at)
    THEN RAISE EXCEPTION 'Accepted source snapshot is immutable'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER source_snapshot_immutable BEFORE UPDATE ON source
    FOR EACH ROW EXECUTE FUNCTION guard_source_snapshot();

-- Public, de-identified research is explicit. This does not enable institution deployment.
ALTER TABLE study DROP CONSTRAINT study_environment_check;
ALTER TABLE study ADD CHECK (environment IN ('synthetic','public_research'));
