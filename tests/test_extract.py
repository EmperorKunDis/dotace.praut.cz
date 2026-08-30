import datetime as dt
import unittest

from scripts.dotace import extract

TODAY = dt.date(2026, 8, 30)


class PeriodTests(unittest.TestCase):
    def test_labelled_opening_and_closing(self):
        text = "Zahájení příjmu žádostí: 29. 4. 2026 Ukončení příjmu žádostí: 25. 9. 2026"

        self.assertEqual(extract.extract_period(text), ("2026-04-29", "2026-09-25"))

    def test_weak_labels_are_used_only_as_fallback(self):
        """`Platnost od/do` je u části portálů skutečné okno příjmu žádostí."""
        text = "Číslo: 111 Platnost od: 30. 7. 2026 09:00 Platnost do: 24. 9. 2026 12:00"

        self.assertEqual(extract.extract_period(text), ("2026-07-30", "2026-09-24"))

    def test_strong_label_beats_weak_one(self):
        text = (
            "Platnost od: 1. 1. 2020 Platnost do: 31. 12. 2030 "
            "Ukončení příjmu žádostí: 25. 9. 2026"
        )

        _opening, closing = extract.extract_period(text)
        self.assertEqual(closing, "2026-09-25")

    def test_publication_date_is_not_a_deadline(self):
        text = "Výzva byla vyhlášena 3. 2. 2020. Poslední aktualizace 4. 5. 2021."

        self.assertEqual(extract.extract_period(text), ("", ""))

    def test_date_range_without_labels(self):
        text = "Termín pro podání žádosti: 28. 8. 2026 - 16. 10. 2026"

        self.assertEqual(extract.extract_period(text), ("2026-08-28", "2026-10-16"))

    def test_word_month_is_parsed(self):
        text = "Příjem žádostí do 15. října 2026"

        _opening, closing = extract.extract_period(text)
        self.assertEqual(closing, "2026-10-15")


class StatusTests(unittest.TestCase):
    def test_open_call_is_active(self):
        self.assertEqual(extract.status_for("2026-01-01", "2026-12-31", TODAY)[1], "active")

    def test_future_call_is_upcoming(self):
        self.assertEqual(extract.status_for("2026-10-01", "2026-12-31", TODAY)[1], "upcoming")

    def test_past_call_is_completed(self):
        self.assertEqual(extract.status_for("2020-01-01", "2020-09-30", TODAY)[1], "completed")

    def test_call_closing_today_is_still_active(self):
        self.assertEqual(extract.status_for("", "2026-08-30", TODAY)[1], "active")

    def test_missing_dates_are_unknown(self):
        self.assertEqual(extract.status_for("", "", TODAY)[1], "unknown")


class ParameterTests(unittest.TestCase):
    def test_allocation_with_currency_after_amount(self):
        self.assertEqual(extract.extract_allocation("Celková alokace výzvy je 200 000 000 Kč."), 200_000_000)

    def test_allocation_with_currency_in_label(self):
        self.assertEqual(extract.extract_allocation("Alokace v Kč: 50 000 000"), 50_000_000)

    def test_allocation_scales_millions(self):
        self.assertEqual(extract.extract_allocation("Alokace činí 70 mil. Kč"), 70_000_000)

    def test_small_unlabelled_amounts_are_ignored(self):
        """Bez štítku `alokace` je drobná částka spíš minimální výše projektu."""
        self.assertEqual(extract.extract_allocation("Podpora od 50 000 Kč na projekt."), 0)

    def test_euro_amounts_are_not_reported_as_czk(self):
        self.assertEqual(extract.extract_allocation("Alokace 2 000 000 EUR"), 0)

    def test_support_rate_needs_a_label(self):
        self.assertEqual(extract.extract_support_rate("Míra podpory až 60 % způsobilých výdajů."), 60)
        self.assertEqual(extract.extract_support_rate("Projekt splnil 100 % kritérií."), 0)

    def test_applicants_and_business_flag(self):
        applicants = extract.extract_applicants("Oprávnění žadatelé: obce a malé a střední podniky.")

        self.assertIn("msp", applicants)
        self.assertIn("obec_kraj", applicants)
        self.assertTrue(set(applicants) & extract.BUSINESS_APPLICANTS)

    def test_regions_are_detected(self):
        self.assertEqual(extract.extract_regions("Projekt v Karlovarském kraji."), [])
        self.assertEqual(extract.extract_regions("Výzva pro Karlovarský kraj."), ["Karlovarský kraj"])

    def test_program_is_recognised_from_full_name(self):
        self.assertEqual(extract.infer_program("Operační program Spravedlivá transformace"), "OP ST")
        self.assertEqual(extract.infer_program("podpora z OP TAK"), "OP TAK")

    def test_call_code_from_text(self):
        self.assertEqual(extract.extract_code_from_text("Číslo: 111 Platnost od"), "111")


class TitleFilterTests(unittest.TestCase):
    def test_navigation_titles_are_rejected(self):
        for title in ("Dokumenty", "Dokumenty k výzvě", "AIS MPO - portál", "Nastavení cookies", "Výzvy"):
            with self.subTest(title=title):
                self.assertTrue(extract.is_blacklisted_title(title))

    def test_real_call_titles_pass(self):
        for title in ("103. výzva – Obnovitelné zdroje energie", "Poradenství – výzva III"):
            with self.subTest(title=title):
                self.assertFalse(extract.is_blacklisted_title(title))

    def test_classification_of_events_and_calls(self):
        self.assertEqual(extract.classify("Seminář pro žadatele", "https://a.test/", "")[1], "event")
        self.assertEqual(extract.classify("Inovační vouchery – výzva IV", "https://a.test/", "")[1], "grant")


if __name__ == "__main__":
    unittest.main()
