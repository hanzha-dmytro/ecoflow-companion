"""Model identification uses upstream's serial-prefix matching and never guesses."""

from ecoflow_companion.infrastructure.ecoflow.discovery import identify


def test_delta3_classic_prefix_matches_upstream_class():
    ident = identify("P321ZZZZ12345678")

    assert ident.upstream_module == "delta3_classic"
    assert ident.upstream_model == "Delta 3 Classic"
    assert ident.table_name == "EcoFlow DELTA 3 Classic"


def test_unknown_prefix_has_no_upstream_class():
    ident = identify("XQ99ZZZZ12345678")

    assert ident.upstream_class is None
    assert ident.upstream_module is None
    assert ident.table_name is None


def test_name_table_hit_without_device_class_is_not_support():
    # "D3" is in upstream's name table (DELTA 1300) but no device class decodes it
    ident = identify("D3ZZZZZZ12345678")

    assert ident.upstream_class is None
    assert ident.table_name == "EcoFlow DELTA (1300)"
