from itertools import product
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from helpdesk import settings as helpdesk_settings
from helpdesk.validators import validate_file_extension


@pytest.mark.parametrize("enabled,unlisted", list(product((False, True), repeat=2)))
@pytest.mark.parametrize(
    "name,plain_extension",
    [("detail.TXT", True), ("detail", False), ("detail.", False)],
    ids=["extension", "blank", "dot"],
)
def test_attachment_validation_decision(
    monkeypatch, enabled, unlisted, name, plain_extension
):
    suffix = {"detail.TXT": ".txt", "detail": "", "detail.": "."}[name]
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_VALIDATE_ATTACHMENT_TYPES", enabled
    )
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_VALID_EXTENSIONS", set() if unlisted else {suffix}
    )
    if enabled and not plain_extension:
        with pytest.raises(
            ValidationError, match="Attachments must have a file extension"
        ):
            validate_file_extension(SimpleNamespace(name=name))
    elif enabled and unlisted:
        with pytest.raises(ValidationError, match="Unsupported file extension: .txt"):
            validate_file_extension(SimpleNamespace(name=name))
    else:
        assert validate_file_extension(SimpleNamespace(name=name)) is None
