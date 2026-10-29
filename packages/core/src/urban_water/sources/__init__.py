"""Configured acquisition; publication remains in the common ingestion pipeline."""

from urban_water.sources.config import SourceSpec, load_source


def collect(settings, config_path, name):
    """Accept one immutable object. This is not a paginated provider API client."""
    from urban_water.ingestion import accept
    from urban_water.sources.readers import get_reader

    spec = load_source(config_path, name)
    if spec.data_kind != settings.environment:
        raise ValueError("source data kind must match the runtime environment")
    with get_reader(spec.reader).acquire(spec, settings.max_source_bytes) as path:
        return accept(settings, path, source=spec)


__all__ = ["SourceSpec", "collect", "load_source"]
