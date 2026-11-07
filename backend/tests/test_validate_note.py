import pytest

from app.generator.note import build_note, reference_line, source_line
from app.generator.validate import validate_card

OK = dict(text="Wczoraj w końcu {{c1::osiągnąłem cel}}.", translation="Вчера я наконец <b>достиг цели</b>.",
          expression="osiągnąć cel", form_used="osiągnąłem cel", is_base_form=False, language="pl")


def test_valid():
    assert validate_card(OK) == []


@pytest.mark.parametrize("patch,frag", [
    ({"text": "Brak cloze."}, "no {{c1"),
    ({"text": "To {{c1::jest"}, "no {{c1"),
    ({"text": "To {{c2::jest}}.", "form_used": "jest"}, "only c1"),
    ({"text": "A {{c1::x}} {{c2::y}}", "form_used": "x"}, "only c1"),
    ({"text": "A {{c1::x}} }}", "form_used": "x"}, "unbalanced"),
    ({"translation": "Нет жирного"}, "<b>"),
    ({"form_used": "osiągnął cel"}, "form_used"),
    ({"expression": " "}, "expression"),
    ({"language": "polish"}, "language"),
    ({"is_base_form": True}, "is_base_form"),
])
def test_invalid(patch, frag):
    errs = validate_card({**OK, **patch})
    assert any(frag in e for e in errs), errs


def test_split_expression():
    c = {**OK, "text": "Je {{c1::ne}} le vois {{c1::jamais}}.", "form_used": "ne … jamais", "expression": "ne … jamais",
         "is_base_form": True, "language": "fr"}
    assert validate_card(c) == []
    c["form_used"] = "ne jamais"
    assert validate_card(c) == []


def test_base_form_ok():
    assert validate_card({**OK, "form_used": "Osiągnąć  cel", "text": "Chcę {{c1::osiągnąć cel}}.",
                          "is_base_form": True}) == []


def test_note_variants():
    assert reference_line("dom", True, {}) == ""
    assert reference_line("osiągnąć cel", False, {}) == "Base form: osiągnąć cel"
    assert reference_line("dom", False, {"gender": "m"}) == "Base form: dom (m.)"
    assert reference_line("dom", True, {"gender": "m"}) == "Base form: dom (m.)"
    assert reference_line("pain", False, {"gender": "m", "article": "le"}) == "Base form: le pain"
    assert reference_line("maison", False, {"gender": "f", "article": "la"}) == "Base form: la maison"
    assert reference_line("arbre", False, {"gender": "m", "article": "l'"}) == "Base form: l'arbre (m.)"
    assert reference_line("skakać", False, {"aspect_pair": "skakać / skoczyć"}) == "Base form: skakać / skoczyć"
    assert reference_line("osiągnąć cel", False, {"aspect_pair": "osiągać / osiągnąć"}) == \
        "Base form: osiągać / osiągnąć cel"


def test_source_lines():
    assert source_line("readwise", "Title", "Author") == "Title — Author · Kindle"
    assert source_line("zotero", "Title", "Author") == "Title — Author · Zotero"
    assert source_line("zotero", "Title", None) == "Title · Zotero"
    assert source_line("gsheets", None, None) == "Notes"


def test_build_note_owner_example():
    n = build_note("osiągnąć cel", False, {"aspect_pair": "osiągać / osiągnąć"}, "gsheets", None, None)
    assert n == 'Base form: osiągać / osiągnąć cel<br><div class="pc-source">Notes</div>'
    assert build_note("dom", True, {}, "readwise", "T", "A") == '<div class="pc-source">T — A · Kindle</div>'
