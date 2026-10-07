"""Docling adapter in a separate process; does not implement document parsing."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


def main():
    # Process-local deny-by-default network boundary, in addition to deployment egress rules.
    class OfflineSocket(socket.socket):
        def connect(self, *args, **kwargs):
            raise PermissionError("Document worker has no network access")

        def connect_ex(self, *args, **kwargs):
            raise PermissionError("Document worker has no network access")

    socket.socket = OfflineSocket
    os.environ["HF_HUB_OFFLINE"] = "1"
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    path, output, models = sys.argv[1:4]
    options = PdfPipelineOptions()
    options.enable_remote_services = False
    options.do_ocr = False  # OCR is a separate, explicitly validated profile.
    if models:
        options.artifacts_path = Path(models)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    result = converter.convert(path)
    document = result.document
    native = document.export_to_dict()
    blocks = []
    for item, _ in document.iterate_items():
        text = getattr(item, "text", "")
        if not text and hasattr(item, "export_to_markdown"):
            text = item.export_to_markdown(doc=document)
        if text:
            provenance = [prov.model_dump(mode="json") for prov in getattr(item, "prov", [])]
            blocks.append(
                {
                    "locator": item.self_ref,
                    "text": text,
                    "metadata": {
                        "parser": "docling",
                        "provenance": provenance,
                        "ocr": "DISABLED",
                        "diagram_coverage": "NOT_ANALYZED",
                    },
                }
            )
    Path(output).write_text(json.dumps({"native": native, "blocks": blocks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
