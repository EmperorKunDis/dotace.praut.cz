import unittest
from pathlib import Path

from scripts.dotace import parsing

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParsingTests(unittest.TestCase):
    def test_unbalanced_svg_does_not_blind_parser(self):
        """Regrese: jeden nezavřený <svg> dřív zahodil zbytek dokumentu."""
        doc = parsing.parse(fixture("blinding_svg.html"), "https://example.test/vyzvy")

        urls = [link.url for link in doc.links]
        self.assertIn("https://example.test/vyzvy/prvni-vyzva/", urls)
        self.assertIn("https://example.test/vyzvy/druha-vyzva/", urls)
        self.assertEqual(doc.title, "Přehled výzev")

    def test_title_falls_back_to_document_title_without_h1(self):
        """Regrese: dřív se bral první nadpis, tedy `Dokumenty k výzvě` ze sidebaru."""
        doc = parsing.parse(fixture("no_h1.html"), "https://example.test/dotace/103-vyzva/")

        self.assertEqual(doc.title_source, "title")
        self.assertTrue(doc.title.startswith("103. výzva"))
        self.assertNotIn("Dokumenty k výzvě", doc.title)

    def test_h1_wins_over_document_title(self):
        doc = parsing.parse(fixture("detail_with_h1.html"), "https://example.test/vyzva")

        self.assertEqual(doc.title_source, "h1")
        self.assertEqual(doc.title, "Poradenství – výzva I")

    def test_navigation_is_not_part_of_content(self):
        doc = parsing.parse(fixture("detail_with_h1.html"), "https://example.test/vyzva")

        self.assertNotIn("Domů Výzvy Kontakty", doc.text)

    def test_attachments_are_collected_with_format(self):
        doc = parsing.parse(fixture("detail_with_h1.html"), "https://example.test/vyzva")
        attachments = parsing.attachments_from(doc.links)

        self.assertEqual(len(attachments), 2)
        self.assertEqual(attachments[0]["url"], "https://example.test/files/vyzva.pdf")
        self.assertEqual(attachments[0]["format"], "PDF")
        self.assertEqual(attachments[1]["format"], "DOCX")

    def test_common_title_suffix_is_detected_and_stripped(self):
        titles = [
            "103. výzva – Obnovitelné zdroje – Operační program Životní prostředí",
            "104. výzva – Podpora v krajině – Operační program Životní prostředí",
            "105. výzva – Srážkové vody – Operační program Životní prostředí",
        ]

        suffix = parsing.common_title_suffix(titles)

        self.assertIn("Operační program Životní prostředí", suffix)
        self.assertEqual(
            parsing.strip_title_suffix(titles[0], suffix),
            "103. výzva – Obnovitelné zdroje",
        )

    def test_common_title_suffix_needs_enough_samples(self):
        self.assertEqual(parsing.common_title_suffix(["Jediný titulek – Web"]), "")

    def test_canonical_url_drops_fragment(self):
        self.assertEqual(
            parsing.canonical_url("https://example.test/a?b=1#kotva"),
            "https://example.test/a?b=1",
        )


if __name__ == "__main__":
    unittest.main()
