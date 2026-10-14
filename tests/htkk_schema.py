"""Optional validation of generated HTKK XML against the official XSDs.

The schemas ship inside the HTKK application (InterfaceTemplates/
TKhaiThue.xsd, TKhaiThue_DBHC.xsd and Validate/*.xsd) and are not
redistributed with VFAS. To validate, copy that folder somewhere and
run:  HTKK_XSD_DIR=/path/to/InterfaceTemplates/Validate pytest
(needs lxml). Without it only the structural assertions run.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def validate(data: bytes, xsd_name: str) -> None:
    folder = os.environ.get("HTKK_XSD_DIR")
    if not folder:
        return
    from lxml import etree

    text = (Path(folder) / xsd_name).read_text(encoding="utf-8")
    # The official BCTC schema declares a few names with a trailing
    # space ("PL_CDTK "); XSD collapses whitespace in names, libxml2
    # does not.
    text = re.sub(r'name="([A-Za-z0-9_.]+) "', r'name="\1"', text)
    parser = etree.XMLParser(no_network=True, resolve_entities=False)
    schema = etree.XMLSchema(
        etree.fromstring(text.encode("utf-8"), parser, base_url=folder + "/")
    )
    doc = etree.fromstring(data, parser)
    assert schema.validate(doc), [e.message for e in schema.error_log][:5]
