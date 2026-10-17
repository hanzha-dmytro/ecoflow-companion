import logging

from ecoflow_companion.redaction import (
    RedactingFormatter,
    Redactor,
    auth_digest,
    mask_address,
    mask_device_name,
    mask_serial,
)

ADDRESS = "AA:BB:CC:DD:57:2E"
SERIAL = "P321ZZZZ12345678"
USER_ID = "1234567890123456789"


def test_mask_helpers():
    assert mask_address(ADDRESS) == "**:**:**:**:57:2E"
    assert mask_address("not-an-address") == "<address>"
    assert mask_serial(SERIAL) == "P321************"
    assert mask_device_name("EF-P32E2666") == "EF-P3******"


def test_redactor_masks_all_registered_forms():
    r = Redactor()
    r.add_address(ADDRESS)
    r.add_serial(SERIAL)
    r.add_credentials(USER_ID, SERIAL)

    text = (
        f"logger aa:bb:cc:dd:57:2e file AA_BB_CC_DD_57_2E sn {SERIAL} "
        f"uid {USER_ID} auth {auth_digest(USER_ID, SERIAL)}"
    )
    out = r.apply(text)

    for leaked in (ADDRESS.lower(), "AA_BB_CC_DD_57_2E", SERIAL, USER_ID):
        assert leaked.lower() not in out.lower()
    assert auth_digest(USER_ID, SERIAL) not in out
    assert "**:**:**:**:57:2E" in out
    assert "P321************" in out


def test_redactor_without_registrations_is_identity():
    assert Redactor().apply("nothing to hide") == "nothing to hide"


def test_formatter_redacts_logger_name_and_args():
    r = Redactor()
    r.add_address(ADDRESS)
    r.add_secret(USER_ID)
    formatter = RedactingFormatter(r, "%(name)s: %(message)s")
    record = logging.LogRecord(
        name=f"eflib.connection - {ADDRESS}",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="user %s",
        args=(USER_ID,),
        exc_info=None,
    )

    out = formatter.format(record)

    assert ADDRESS not in out
    assert USER_ID not in out
    assert out == "eflib.connection - **:**:**:**:57:2E: user <redacted>"
