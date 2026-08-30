import argparse
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_data
from scripts.dotace import adapters, item as item_builder, parsing

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 8, 30)

SOURCE = {
    "id": "test-source",
    "name": "Testovací zdroj",
    "url": "https://example.test/vyzvy",
    "topics": "OP TAK",
    "integration": "automatizováno",
    "adapter": "html_catalog",
}


def make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        sources=build_data.DEFAULT_SOURCES,
        output=build_data.DEFAULT_DATA,
        current_data=build_data.DEFAULT_DATA,
        limit_per_source=10,
        timeout=5.0,
        delay=0.0,
        offline_current=True,
        fresh=False,
        preserve_current=True,
        ignore_thresholds=False,
        ignore_robots=True,
        verbose=False,
        only="",
        today=TODAY,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class ItemBuildingTests(unittest.TestCase):
    def test_detail_page_becomes_normalized_item(self):
        html = (FIXTURES / "no_h1.html").read_text(encoding="utf-8")
        doc = parsing.parse(html, "https://example.test/dotace/103-vyzva/")

        item = item_builder.build_item(SOURCE, "https://example.test/dotace/103-vyzva/", doc, today=TODAY)

        self.assertTrue(item["title"].startswith("103. výzva"))
        self.assertEqual(item["code"], "103")
        self.assertEqual(item["opening_date"], "2026-04-29")
        self.assertEqual(item["closing_date"], "2026-09-25")
        self.assertEqual(item["status_code"], "active")
        self.assertEqual(item["allocation_czk"], 200_000_000)
        self.assertEqual(item["support_rate_pct"], 60)
        self.assertIn("msp", item["applicant_types"])
        self.assertTrue(item["for_business"])
        self.assertEqual(item["attachments"][0]["format"], "PDF")
        self.assertEqual(item["deadline"], item["closing_date"])

    def test_navigation_page_is_rejected(self):
        html = "<html><head><title>x</title></head><body><h1>Dokumenty k výzvě</h1></body></html>"
        doc = parsing.parse(html, "https://example.test/dokumenty")

        self.assertIsNone(item_builder.build_item(SOURCE, "https://example.test/dokumenty", doc, today=TODAY))

    def test_title_suffix_is_stripped_across_a_source(self):
        items = [
            {"title": f"{n}. výzva – Téma {n} – Operační program Životní prostředí", "title_source": "title"}
            for n in (103, 104, 105)
        ]

        item_builder.apply_title_suffix(items)

        self.assertEqual(items[0]["title"], "103. výzva – Téma 103")

    def test_stable_id_survives_reruns(self):
        first = item_builder.stable_item_id("src", "https://example.test/a#kotva")
        second = item_builder.stable_item_id("src", "https://example.test/a")

        self.assertEqual(first, second)


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.source = dict(SOURCE, adapter="stub", min_items=3)
        adapters.ADAPTERS["stub"] = self._stub
        self.addCleanup(adapters.ADAPTERS.pop, "stub", None)
        self.produced = 1

    def _stub(self, source, fetcher=None, limit=0, today=None):
        result = adapters.SourceResult(source_id=source["id"])
        result.items = [{"title": f"Výzva {n}", "source_url": f"https://example.test/{n}"} for n in range(self.produced)]
        return result

    def test_source_below_threshold_is_reported(self):
        _items, _warnings, errors = build_data.collect_sources([self.source], fetcher=None, limit=10, today=TODAY)

        self.assertEqual(len(errors), 1)
        self.assertIn("alespoň 3", errors[0])

    def test_source_meeting_threshold_is_silent(self):
        self.produced = 3

        _items, _warnings, errors = build_data.collect_sources([self.source], fetcher=None, limit=10, today=TODAY)

        self.assertEqual(errors, [])

    def test_failing_adapter_does_not_abort_the_run(self):
        def explode(source, **_kwargs):
            raise RuntimeError("zdroj je nedostupný")

        adapters.ADAPTERS["stub"] = explode

        items, warnings, _errors = build_data.collect_sources([self.source], fetcher=None, limit=10, today=TODAY)

        self.assertEqual(items, [])
        self.assertTrue(any("zdroj je nedostupný" in w for w in warnings))


class LifecycleTests(unittest.TestCase):
    def test_status_is_recomputed_from_todays_date(self):
        item = {"opening_date": "2026-01-01", "closing_date": "2026-08-29", "status_code": "active", "status": "Probíhající"}

        build_data.refresh_status(item, TODAY)

        self.assertEqual(item["status_code"], "completed")

    def test_long_closed_items_are_pruned(self):
        items = [
            {"closing_date": "2020-09-30"},
            {"closing_date": "2026-08-01"},
            {"closing_date": ""},
        ]

        kept = build_data.prune(items, TODAY)

        self.assertEqual(len(kept), 2)
        self.assertNotIn("2020-09-30", [i["closing_date"] for i in kept])

    def test_fresh_data_replaces_previous_entry(self):
        previous = [{"source_url": "https://example.test/a", "title": "Staré"}]
        fresh = [{"source_url": "https://example.test/a", "title": "Nové"}]

        merged = build_data.merge_items(previous, fresh)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "Nové")

    def test_sorting_puts_open_calls_first(self):
        items = [
            {"status_code": "completed", "closing_date": "2026-01-01", "title": "Ukončená"},
            {"status_code": "active", "closing_date": "2026-12-01", "title": "Probíhající"},
            {"status_code": "upcoming", "closing_date": "2027-01-01", "title": "Připravovaná"},
        ]

        order = [i["title"] for i in build_data.sort_items(items)]

        self.assertEqual(order, ["Probíhající", "Připravovaná", "Ukončená"])

    def test_export_drops_internal_fields_and_trims_text(self):
        entry = build_data.slim({"title": "x", "title_source": "h1", "doc_title": "x", "text": "a" * 5000})

        self.assertNotIn("title_source", entry)
        self.assertNotIn("doc_title", entry)
        self.assertEqual(len(entry["text"]), build_data.EXPORT_TEXT_CHARS)


class ValidationTests(unittest.TestCase):
    def test_broken_contract_is_rejected(self):
        export = {
            "items": [
                {
                    "title": "",
                    "source_url": "",
                    "status_code": "bad",
                    "closing_date": "30. 6. 2026",
                    "attachments": {},
                    "applicant_types": "msp",
                }
            ]
        }

        errors = build_data.validate_export(export)

        self.assertGreaterEqual(len(errors), 6)

    def test_navigation_title_fails_validation(self):
        export = {
            "items": [
                {
                    "title": "Dokumenty k výzvě",
                    "source_url": "https://example.test/a",
                    "status_code": "unknown",
                    "attachments": [],
                    "applicant_types": [],
                }
            ]
        }

        errors = build_data.validate_export(export)

        self.assertTrue(any("navigační šum" in error for error in errors))

    def test_valid_export_has_no_errors(self):
        export = {
            "items": [
                {
                    "title": "103. výzva – Obnovitelné zdroje",
                    "source_url": "https://example.test/a",
                    "status_code": "active",
                    "opening_date": "2026-01-01",
                    "closing_date": "2026-12-31",
                    "attachments": [],
                    "applicant_types": ["msp"],
                }
            ]
        }

        self.assertEqual(build_data.validate_export(export), [])


class OfflineRebuildTests(unittest.TestCase):
    def test_offline_rebuild_keeps_items_and_refreshes_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sources_path = tmp_path / "sources.json"
            current_path = tmp_path / "data.js"
            output_path = tmp_path / "out.js"
            sources_path.write_text(
                json.dumps([dict(SOURCE, adapter="manual", category="Test", for_business=True)]),
                encoding="utf-8",
            )
            current_path.write_text(
                build_data.EXPORT_PREFIX
                + json.dumps(
                    {
                        "generated_at": "2026-01-01T00:00:00+01:00",
                        "stats": {},
                        "items": [
                            {
                                "id": 1,
                                "title": "Probíhající výzva",
                                "type": "Dotace",
                                "type_code": "grant",
                                "status": "Probíhající",
                                "status_code": "active",
                                "opening_date": "2026-01-01",
                                "closing_date": "2026-08-01",
                                "source_url": "https://example.test/a",
                                "source_id": "test-source",
                                "text": "",
                                "attachments": [],
                            }
                        ],
                        "sources": [],
                    }
                )
                + build_data.EXPORT_SUFFIX,
                encoding="utf-8",
            )
            args = make_args(sources=sources_path, current_data=current_path, output=output_path)

            export, warnings = build_data.build_export(args)
            build_data.write_export(output_path, export)
            reloaded = build_data.load_current_export(output_path)

            self.assertEqual(warnings, [])
            self.assertEqual(reloaded["stats"]["total"], 1)
            self.assertEqual(reloaded["items"][0]["status_code"], "completed")
            self.assertEqual(reloaded["items"][0]["source_name"], "Testovací zdroj")
            self.assertTrue(reloaded["sources"][0]["for_business"])

    def test_unknown_adapter_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            sources_path = Path(tmp) / "sources.json"
            sources_path.write_text(json.dumps([dict(SOURCE, adapter="neexistuje")]), encoding="utf-8")

            with self.assertRaises(ValueError):
                build_data.load_sources(sources_path)

    def test_duplicate_source_ids_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            sources_path = Path(tmp) / "sources.json"
            sources_path.write_text(json.dumps([SOURCE, SOURCE]), encoding="utf-8")

            with self.assertRaises(ValueError):
                build_data.load_sources(sources_path)


class ShippedSourcesTests(unittest.TestCase):
    def test_sources_json_is_valid(self):
        sources = build_data.load_sources(build_data.DEFAULT_SOURCES)

        self.assertGreater(len(sources), 15)
        self.assertTrue(any(source.get("for_business") for source in sources))


if __name__ == "__main__":
    unittest.main()


class DedupeTests(unittest.TestCase):
    def test_same_call_from_two_portals_is_merged(self):
        items = [
            {"title": "Space data", "source_id": "dotaceeu", "closing_date": "", "attachments": [], "text": "krátký"},
            {"title": "Space data", "source_id": "eu-ft", "closing_date": "2026-09-03", "attachments": [], "text": "delší popis"},
        ]

        merged = build_data.dedupe_across_sources(items)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_id"], "eu-ft")

    def test_same_title_within_one_source_is_kept(self):
        items = [
            {"title": "Investiční úvěry", "source_id": "pgrlf", "source_url": "https://a.test/1"},
            {"title": "Investiční úvěry", "source_id": "pgrlf", "source_url": "https://a.test/2"},
        ]

        self.assertEqual(len(build_data.dedupe_across_sources(items)), 2)

    def test_attachment_list_is_capped(self):
        entry = build_data.slim({"title": "x", "attachments": [{"url": str(n)} for n in range(80)]})

        self.assertEqual(len(entry["attachments"]), build_data.MAX_ATTACHMENTS)


class FetcherConfigTests(unittest.TestCase):
    def test_per_source_timeout_and_robots_exemption_are_applied(self):
        class FakeFetcher:
            def __init__(self):
                self.robots_exempt_hosts = set()
                self.host_timeouts = {}

        fetcher = FakeFetcher()
        sources = [
            dict(SOURCE, id="a", adapter="manual", url="https://slow.test/x", timeout=75),
            dict(SOURCE, id="b", adapter="manual", url="https://blocked.test/y", ignore_robots=True),
        ]

        build_data.collect_sources(sources, fetcher=fetcher, limit=10, today=TODAY)

        self.assertEqual(fetcher.host_timeouts, {"slow.test": 75.0})
        self.assertEqual(fetcher.robots_exempt_hosts, {"blocked.test"})


class OnlyFlagTests(unittest.TestCase):
    def test_only_rejects_unknown_source_id(self):
        args = make_args(only="neexistuje", offline_current=True)

        with self.assertRaises(ValueError):
            build_data.build_export(args)

    def test_only_is_parsed_from_command_line(self):
        args = build_data.parse_args(["--only", "a,b"])

        self.assertEqual(args.only, "a,b")
