# dotace.praut.cz

Statický přehled dotačních výzev pro GitHub Pages.

## Produkce

- Web: https://dotace.praut.cz
- GitHub Pages source: `main:/`
- Custom domain: `dotace.praut.cz`
- DNS v Cloudflare:

```text
CNAME  dotace  EmperorKunDis.github.io
```

## Aktualizace dat

Ručně:

```bash
python3 scripts/build_data.py --fresh --verbose
```

Offline kontrola bez volání externích zdrojů:

```bash
python3 scripts/build_data.py --offline-current
```

Týdenní GitHub Action spouští refresh dat každé pondělí ráno a commituje změněný `data.js`.

## Kontrola

```bash
python3 -m unittest tests/test_build_data.py
```
