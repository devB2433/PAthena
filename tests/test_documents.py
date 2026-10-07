import pytest

from security_auditor.ingestion import extract_document


def test_offline_docling_word_and_ppt(settings, tmp_path):
    docx = pytest.importorskip("docx")
    pptx = pytest.importorskip("pptx")
    document = docx.Document()
    document.add_heading("订单子系统", 0)
    document.add_paragraph("批量导出必须校验租户范围")
    word = tmp_path / "design.docx"
    document.save(word)
    slides = pptx.Presentation()
    slide = slides.slides.add_slide(slides.slide_layouts[1])
    slide.shapes.title.text = "支付子系统"
    slide.placeholders[1].text = "支付数据需要访问控制"
    deck = tmp_path / "design.pptx"
    slides.save(deck)
    for path, expected in [(word, "租户范围"), (deck, "访问控制")]:
        blocks = extract_document(path, settings)
        assert any(expected in block["text"] for block in blocks)
        assert all(block["metadata"]["parser"] == "docling" for block in blocks)


def test_pdf_refuses_unprepared_models(settings, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="预置"):
        extract_document(pdf, settings)


def test_offline_docling_pdf_with_preloaded_models(settings):
    from dataclasses import replace
    from security_auditor.config import ROOT

    models = ROOT / "models/docling"
    if not models.exists():
        pytest.skip("PDF 模型尚未预置")
    blocks = extract_document(
        ROOT / "tests/fixtures/simple-design.pdf", replace(settings, docling_models=str(models))
    )
    assert any("tenant ownership" in block["text"] for block in blocks)
