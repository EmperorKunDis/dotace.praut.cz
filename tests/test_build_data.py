import argparse
import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_data


class BuildDataTests(unittest.TestCase):
    def test_detail_parser_extracts_normalized_item(self):
        html = """
        <html>
          <head><title>Fallback title</title></head>
          <body>
            <nav>CZ EN Domů OP TAK Výzvy Statistiky Kontakty</nav>
            <main>
              <h1>Testovací výzva I</h1>
              <p>Příjem žádostí je otevřen do 30. 6. 2026.</p>
              <p>Podpora je určena pro malé a střední podniky.</p>
              <a href="/files/pravidla.pdf">Pravidla výzvy</a>
            </main>
          </body>
        </html>
        """
        source = {
            "id": "test-source",
            "name": "Test source",
            "topics": "OP TAK",
        }

        item = build_data.item_from_detail(source, "https://example.test/vyzva", html)

        self.assertEqual(item["title"], "Testovací výzva I")
        self.assertEqual(item["type_code"], "grant")
        self.assertEqual(item["deadline"], "2026-06-30")
        self.assertEqual(item["status_code"], "active")
        self.assertEqual(item["source_id"], "test-source")
        self.assertEqual(item["program"], "OP TAK")
        self.assertEqual(item["attachments"][0]["url"], "https://example.test/files/pravidla.pdf")
        self.assertNotIn("CZ EN Domů", item["text"])

    def test_export_validation_rejects_bad_contract(self):
        export = {
            "items": [
                {
                    "title": "",
                    "source_url": "",
                    "status_code": "bad",
                    "deadline": "30. 6. 2026",
                    "attachments": {},
                }
            ]
        }

        errors = build_data.validate_export(export)

        self.assertGreaterEqual(len(errors), 5)

    def test_offline_current_rewrites_sources_and_preserves_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sources_path = tmp_path / "sources.json"
            current_path = tmp_path / "data.js"
            output_path = tmp_path / "out.js"
            sources = [
                {
                    "id": "api-agentura-optak",
                    "name": "API Agentura - OP TAK",
                    "category": "Fondy EU v ČR",
                    "type": "Sitemap",
                    "url": "https://apiagentura.gov.cz/cs/xmlsitemap.xml",
                    "target_groups": "podniky",
                    "topics": "OP TAK",
                    "notes": "test",
                    "integration": "automatizováno",
                    "adapter": "manual",
                }
            ]
            export = {
                "generated_at": "2026-01-01T00:00:00+01:00",
                "stats": {"total": 1, "active": 0, "completed": 0, "unknown": 1, "sources": 1},
                "items": [
                    {
                        "id": 1,
                        "title": "Existing",
                        "type": "Dotace",
                        "type_code": "grant",
                        "status": "Neznámé",
                        "status_code": "unknown",
                        "deadline": "",
                        "source_url": "https://apiagentura.gov.cz/cs/existing",
                        "summary": "",
                        "text": "Existing text",
                        "attachments": [],
                    }
                ],
                "sources": [],
            }
            sources_path.write_text(json.dumps(sources), encoding="utf-8")
            current_path.write_text(build_data.EXPORT_PREFIX + json.dumps(export) + build_data.EXPORT_SUFFIX, encoding="utf-8")
            args = argparse.Namespace(
                sources=sources_path,
                output=output_path,
                current_data=current_path,
                limit_per_source=5,
                timeout=1,
                offline_current=True,
                fresh=False,
                preserve_current=True,
                strict_noise=False,
                verbose=False,
            )

            rebuilt, warnings = build_data.build_export(args)
            build_data.write_export(output_path, rebuilt)
            parsed = build_data.load_current_export(output_path)

            self.assertEqual(warnings, [])
            self.assertEqual(parsed["stats"]["total"], 1)
            self.assertEqual(parsed["items"][0]["source_id"], "api-agentura-optak")
            self.assertEqual(parsed["sources"][0]["integration"], "automatizováno")


if __name__ == "__main__":
    unittest.main()
