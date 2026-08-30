# dotace.praut.cz

Statický přehled otevřených dotačních výzev, zvýhodněných úvěrů a záruk pro firmy
se sídlem v České republice. Data se sbírají z oficiálních portálů a publikují
jako jeden JSON v `data.js`, který vykresluje `index.html`. Žádný backend.

## Produkce

- Web: https://dotace.praut.cz
- GitHub Pages source: `main:/`
- DNS v Cloudflare: `CNAME  dotace  EmperorKunDis.github.io`

## Jak to funguje

```
sources.json  →  scripts/build_data.py  →  data.js  →  index.html
```

- `sources.json` je katalog zdrojů. Každý zdroj říká, jakým **adaptérem** se čte,
  jaké URL se berou v úvahu a kolik položek se od něj minimálně čeká (`min_items`).
- `scripts/dotace/` obsahuje jádro: HTTP klient s retry a zdvořilými prodlevami,
  parser HTML, extrakci termínů a parametrů výzvy a jednotlivé adaptéry.
- `scripts/build_data.py` zdroje projde, výsledky sloučí, seřadí a zapíše export.

### Adaptéry

| Adaptér | K čemu je |
|---|---|
| `html_catalog` | Katalogová stránka s odkazy na detaily výzev. |
| `wordpress_vyzvy` | Weby operačních programů na společné šabloně (`opzp.cz`, `opst.cz`). |
| `sitemap` | XML sitemapa, když web nemá použitelný výpis. |
| `rss` | Kanál s výzvami (`apiagentura.gov.cz`, `esfcr.cz`). |
| `dotaceeu` | Centrální portál evropských fondů. |
| `sedia_api` | Oficiální API portálu EU Funding & Tenders. |
| `reference_page` | Zdroj bez samostatných detailů — eviduje se rozcestník. |
| `manual` | Zdroj sledovaný ručně; do dat nic nepřidává. |

## Vývoj

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Testy (běží offline, HTML fixtury jsou v `tests/fixtures/`):

```bash
.venv/bin/python -m unittest discover -s tests -t . -v
```

Přepočet bez sítě — jen zaktualizuje stavy podle dnešního data a katalog zdrojů:

```bash
.venv/bin/python scripts/build_data.py --offline-current
```

Plný sběr. Bez `--fresh` se stávající položky zachovají a nová data je přepíšou,
takže výpadek jednoho zdroje neodstraní jeho výzvy z webu:

```bash
.venv/bin/python scripts/build_data.py --verbose
```

Když se rozbije jeden zdroj, není nutné projíždět všechny — ostatní se převezmou
ze stávajícího `data.js`:

```bash
.venv/bin/python scripts/build_data.py --only sfzp,tacr-souteze --verbose
```

Náhled webu lokálně:

```bash
.venv/bin/python -m http.server 8000
# http://localhost:8000
```

## Pojistky

- **Prahy `min_items`** — když zdroj dodá míň položek, build skončí chybou.
  Bez toho by se dala tichá ztráta dat poznat jen z počtu na webu.
- **Kontrola exportu** — položka bez názvu, s neplatným stavem, rozbitým datem
  nebo s navigačním titulkem (`Dokumenty k výzvě`, `Detail výzvy`) build shodí.
- **Prořezávání** — výzvy uzavřené déle než 400 dní se z exportu odstraní.
- **Varování v datech** — `export.warnings` se propisuje na web, takže je z webu
  poznat, že se některý zdroj neozval.
- **Selhání CI** založí issue se štítkem `data-refresh`.

## robots.txt

Klient robots.txt respektuje. Jedinou výjimkou je `mpo.gov.cz`, které plošně
zakazuje všechny roboty kromě vyhledávačů; zdroj má proto v `sources.json`
`"ignore_robots": true`. Sběr běží jednou týdně s prodlevou mezi požadavky.

## Aktualizace

GitHub Action `update-data.yml` běží každé pondělí ráno, spustí testy, přegeneruje
`data.js` a commitne změnu. Ručně jde spustit přes *Run workflow*, kde lze zapnout
volbu `fresh` pro úplnou přestavbu.
