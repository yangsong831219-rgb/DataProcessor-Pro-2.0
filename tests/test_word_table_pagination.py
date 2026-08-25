from docx import Document
from docx.oxml.ns import qn

from dp_engine.report_builder.word_builder import _render_md_table_as_word


def test_markdown_table_rows_do_not_split_and_header_repeats():
    doc = Document()
    _render_md_table_as_word(
        doc,
        [
            ["传感器", "模式", "评级"],
            ["A1", "dual_working", "良"],
            ["A2", "dual_working", "FAIL"],
        ],
    )

    table = doc.tables[0]
    assert table.rows[0]._tr.get_or_add_trPr().find(qn("w:tblHeader")) is not None
    for row in table.rows:
        assert row._tr.get_or_add_trPr().find(qn("w:cantSplit")) is not None
