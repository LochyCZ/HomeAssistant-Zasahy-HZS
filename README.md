# 🚒 Zásahy HZS

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-03A9F4.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/github/v/release/LochyCZ/HomeAssistant-Zasahy-HZS)](https://github.com/LochyCZ/HomeAssistant-Zasahy-HZS/releases)

Integrace pro Home Assistant, která sleduje **výjezdy hasičů v osmi krajích ČR**.
Z veřejných přehledů krajských HZS dělá senzory, body na mapě a eventy pro notifikace.
Kde to zdroj dovolí, ukáže i **zasahující jednotky** a **vzdálenost zásahu od tvého domova**.

> *English: Home Assistant custom integration that tracks fire brigade (HZS) incidents in eight
> Czech regions. Provides sensors, map entities, events for new incidents and status changes,
> responding units and distance from home. The UI is in Czech and English.*

## Podporované kraje

| Kraj | Zdroj dat | Jednotky | Mapa a vzdálenost | Historie |
|---|---|:---:|:---:|---|
| Jihomoravský | [udalosti.firebrno.cz](https://udalosti.firebrno.cz/) | ✅ | ✅ | 7 dní |
| Královéhradecký | [udalostikhk.hzscr.cz](https://udalostikhk.hzscr.cz/) | ✅ | ✅ | 7 dní |
| Moravskoslezský | [webohled.hzsmsk.cz](http://webohled.hzsmsk.cz/) | ✅ | ✅ | 7 dní |
| Pardubický | [hzspa.cz/vyjezdy](https://www.hzspa.cz/vyjezdy/aktualni-vyjezdy.php) | ✅ | – | dnes a včera |
| Středočeský | [Portál krizového řízení](https://pkr.kr-stredocesky.cz/pkr/zasahy-jpo/) | – | – | 7 dní ¹ |
| Ústecký | [udalosti.hzsulk.cz](https://udalosti.hzsulk.cz/) | ✅ | ✅ | 7 dní |
| Vysočina | [webohled.hasici-vysocina.cz](http://webohled.hasici-vysocina.cz/udalosti/) | ✅ | ✅ | 7 dní |
| Zlínský | [webohledzlk.hzscr.cz](https://webohledzlk.hzscr.cz/udalosti/) | ✅ | ✅ | 7 dní |

¹ U celého Středočeského kraje nejvýš 150 posledních zásahů, u okresu celý týden.

Šest krajů běží na stejném systému HZS (WebOhled). Středočeský a Pardubický mají vlastní
weby, které integrace čte zvlášť, viz [Zvláštnosti krajů](#zvláštnosti-krajů).

Pokud systém WebOhled používá i jiný kraj, zvol v nastavení **Jiná adresa…** a vlož adresu
jeho přehledu událostí. Adresu API integrace dohledá sama.

## Co umí

- **Výběr kraje a okresu**, případně jen vybraných typů událostí (třeba jen požáry
  a dopravní nehody).
- **Senzory:** dnešní zásahy, probíhající zásahy, přehled za 7 dní, poslední zásah
  a nejbližší dnešní zásah.
- **Eventy** při novém výjezdu a při změně stavu, například když se zásah uzavře.
- **Jednotky a technika:** kdo vyjel a s čím.
- **Vzdálenost od domova** u každého zásahu, takže notifikace snadno omezíš třeba
  na 10 km kolem domu.
- **Mapa:** zásahy za posledních 24 hodin a všechny probíhající jako entity `geo_location`.
- **Víc krajů a okresů najednou:** každý je samostatná instance.
- **Nastavení celé přes UI**, bez YAML.
- **Žádné staré notifikace po restartu:** první načtení se jen zapamatuje.
- **Šetrnost k databázi:** seznamy zásahů a texty se neukládají do recorderu.

## Instalace

### HACS (doporučeno)

[![Otevřít v HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=LochyCZ&repository=HomeAssistant-Zasahy-HZS&category=integration)

Nebo ručně: **HACS → ⋮ → Custom repositories**, vlož `https://github.com/LochyCZ/HomeAssistant-Zasahy-HZS`
a jako typ zvol **Integration**. Pak integraci nainstaluj a restartuj Home Assistant.

### Ručně

1. Stáhni si poslední [release](https://github.com/LochyCZ/HomeAssistant-Zasahy-HZS/releases).
2. Složku `custom_components/hzs_zasahy` zkopíruj do `/config/custom_components/`.
3. Restartuj Home Assistant.

## Nastavení

[![Přidat integraci](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hzs_zasahy)

1. Otevři **Nastavení → Zařízení a služby → Přidat integraci → Zásahy HZS**.
2. Vyber kraj.
3. Vyber okres, nebo celý kraj, a případně typy událostí. Bez výběru typů sleduješ všechny.

Instance se pojmenuje sama, například *HZS Ústecký kraj – okres Most*. Další kraj nebo okres
přidáš stejným postupem jako další instanci.

V **Konfigurovat** pak můžeš změnit:

| Volba | Výchozí | Popis |
|---|---|---|
| Typy událostí | všechny | např. jen požáry a dopravní nehody |
| Interval kontroly | 5 min (Středočeský 10 min) | 2–60 minut |
| Načítat jednotky a techniku | zapnuto | jen kraje na systému WebOhled |
| Zobrazovat zásahy na mapě | zapnuto | jen kraje na systému WebOhled |

Vzdálenost se počítá od polohy domova, kterou máš nastavenou v **Nastavení → Systém → Obecné**.

## Entity

ID entit se tvoří z názvu instance. Příklady jsou pro HA v češtině a instanci
*HZS Ústecký kraj – okres Most*.

| Entita | Stav | Atributy |
|---|---|---|
| `sensor.hzs_ustecky_kraj_okres_most_zasahy_dnes` | počet dnešních zásahů | `zasahy` |
| `sensor.hzs_ustecky_kraj_okres_most_probihajici_zasahy` | počet probíhajících zásahů | `zasahy` |
| `sensor.hzs_ustecky_kraj_okres_most_zasahy_za_7_dni` | počet zásahů za 7 dní | `zasahy` (posledních 50) |
| `sensor.hzs_ustecky_kraj_okres_most_posledni_zasah` | název posledního zásahu | pole zásahu |
| `sensor.hzs_ustecky_kraj_okres_most_cas_posledniho_zasahu` | čas (timestamp) | – |
| `sensor.hzs_ustecky_kraj_okres_most_nejblizsi_dnesni_zasah` | vzdálenost v km | pole zásahu |
| `geo_location.*` | vzdálenost v km | typ, stav, obec, jednotky… |

Přesná ID si ověříš ve **Vývojářské nástroje → Stavy**.

### Pole zásahu

Tato pole mají položky v atributu `zasahy` a stejná pole přicházejí i v datech eventů.
Co zdroj neposkytuje, je `null` nebo prázdný seznam.

| Pole | Příklad | Poznámka |
|---|---|---|
| `id` | `356494042` | |
| `nazev` | `Záchrana osob a zvířat – uzavřené prostory, výtah – Litvínov` | |
| `typ` | `Záchrana osob a zvířat` | |
| `podtyp` | `uzavřené prostory, výtah` | |
| `stav` | `Otevřená, SaP na místě` | text ze zdroje |
| `stav_id` | `420` | |
| `aktivni` | `true` | zásah probíhá |
| `okres` | `Most` | |
| `obec` | `Litvínov` | |
| `cast_obce` | `Hamr` | |
| `ulice` | `Přátelství` | |
| `silnice` | `D7` | volný text z dispečinku |
| `orp` | `Litvínov` | |
| `misto` | `Litvínov, Hamr, Přátelství` | obec, část obce a ulice dohromady |
| `poznamka` | `Osoby vyproštěny z výtahu.` | |
| `cas` | `2026-10-15T22:05:33+02:00` | čas ohlášení |
| `latitude`, `longitude` | `50.59274`, `13.57331` | |
| `vzdalenost_km` | `12.3` | od polohy domova |
| `jednotky` | `["HS Litvínov"]` | |
| `technika` | `[{"typ": "cisternová automobilová stříkačka < 30", "jednotka": "HS Litvínov", "pocet": 1, "aktualni_pocet": 1}]` | |
| `stupen` | `II.` | stupeň poplachu, jen Středočeský kraj |

## Eventy

| Event | Kdy se vyšle | Pole navíc |
|---|---|---|
| `hzs_zasahy_novy_zasah` | Objeví se nový zásah. | `kraj`, `entry_id` |
| `hzs_zasahy_zmena_stavu` | Zásah změní stav, např. z probíhajícího na uzavřený. | `kraj`, `entry_id`, `predchozi_stav`, `predchozi_stav_id` |

Když máš víc instancí, rozlišíš je podle `kraj` nebo `entry_id`.

## Příklady

### Notifikace na zásahy do 10 km od domova

Pro kraje se souřadnicemi (všechny kromě Středočeského a Pardubického):

```yaml
alias: "Hasiči – výjezd v okolí"
mode: queued
triggers:
  - trigger: event
    event_type: hzs_zasahy_novy_zasah
conditions:
  - condition: template
    value_template: >
      {{ trigger.event.data.vzdalenost_km is not none
         and trigger.event.data.vzdalenost_km <= 10 }}
actions:
  - action: notify.mobile_app_telefon
    data:
      title: "🚒 {{ trigger.event.data.typ }} – {{ trigger.event.data.obec }}"
      message: >
        {{ trigger.event.data.podtyp }} · {{ trigger.event.data.vzdalenost_km }} km
        {%- if trigger.event.data.jednotky %} · {{ trigger.event.data.jednotky | join(', ') }}{% endif %}
```

### Notifikace pro vybrané obce

Funguje ve všech krajích, Středočeského a Pardubického nevyjímaje:

```yaml
alias: "Hasiči – výjezd v mých obcích"
mode: queued
triggers:
  - trigger: event
    event_type: hzs_zasahy_novy_zasah
conditions:
  - condition: template
    value_template: >
      {{ trigger.event.data.obec in ['Rakovník', 'Lužná', 'Senomaty'] }}
actions:
  - action: notify.mobile_app_telefon
    data:
      title: "🚒 {{ trigger.event.data.typ }} – {{ trigger.event.data.obec }}"
      message: >
        {{ trigger.event.data.podtyp }} · {{ trigger.event.data.stav }}
        {%- if trigger.event.data.stupen %} · stupeň {{ trigger.event.data.stupen }}{% endif %}
```

Další užitečné podmínky:

```jinja
{{ trigger.event.data.typ == 'Požár' }}                {# jen požáry #}
{{ trigger.event.data.typ != 'Planý poplach' }}       {# bez planých poplachů #}
{{ trigger.event.data.kraj == 'Ústecký kraj' }}       {# jen jeden kraj #}
{{ trigger.event.data.okres == 'Pardubice' }}         {# jen jeden okres #}
```

### Oznámení o ukončení zásahu

```yaml
alias: "Hasiči – zásah ukončen"
triggers:
  - trigger: event
    event_type: hzs_zasahy_zmena_stavu
conditions:
  - condition: template
    value_template: >
      {{ not trigger.event.data.aktivni
         and trigger.event.data.obec in ['Most', 'Litvínov'] }}
actions:
  - action: notify.mobile_app_telefon
    data:
      message: "✅ {{ trigger.event.data.nazev }} – {{ trigger.event.data.stav }}"
```

### Karta do dashboardu

```yaml
type: markdown
title: 🚒 Zásahy – okres Most
content: |
  {% set z = state_attr('sensor.hzs_ustecky_kraj_okres_most_zasahy_za_7_dni', 'zasahy') or [] %}
  | Čas | Událost | Místo | km |
  |---|---|---|---|
  {% for i in z[:10] -%}
  | {{ as_timestamp(i.cas) | timestamp_custom('%d.%m. %H:%M') }} | {{ '🔴' if i.aktivni else '⚪' }} {{ i.typ }}{{ ' – ' ~ i.podtyp if i.podtyp and i.podtyp | lower != i.typ | lower }} | {{ i.misto }} | {{ i.vzdalenost_km or '' }} |
  {% endfor %}
```

### Mapa

```yaml
type: map
geo_location_sources:
  - hzs_zasahy
entities:
  - zone.home
hours_to_show: 24
```

## Zvláštnosti krajů

### Kraje na systému WebOhled

Jihomoravský, Královéhradecký, Moravskoslezský, Ústecký, Vysočina a Zlínský kraj mají
přehled událostí s JSON API, ze kterého čte i samotný web. Každá událost má stálé ID,
souřadnice a seznam techniky. Souřadnice (S-JTSK) převádí integrace na GPS sama,
bez externích knihoven.

Jednotlivé weby se přesto v drobnostech liší a integrace to sjednocuje:

- **Čas:** Jihomoravský, Moravskoslezský kraj a Vysočina posílají UTC, Ústecký a Zlínský
  místní čas bez časové zóny.
- **Adresa API:** Zlínský ji má na kořeni serveru (`/api/`), ne pod adresou webu.
- **Číslo kraje:** Moravskoslezský web ho neuvádí, integrace ho zná sama. U vlastní adresy
  se na kraj zeptá.

### Středočeský kraj

Kraj přehled na systému WebOhled nemá. Integrace čte tabulku zásahů z Portálu krizového
řízení kraje.

- **Bez souřadnic a techniky**, takže ani bez mapy a vzdálenosti. Notifikace filtruj
  podle obce.
- **Navíc stupeň poplachu** v poli `stupen` (I., II., III., Z).
- **Čas je poslední aktualizace dat**, ne čas ohlášení.
- **Události nemají ID.** Stejný název a obec s novějším časem do 6 hodin se bere jako
  změna stavu téže události, ne jako nový výjezd.
- **Celý kraj:** jen 150 naposledy aktualizovaných zásahů (3 stránky po 50). Za týden
  jich bývá přes 400, proto pro týdenní přehled nastav konkrétní okres.
- **Výchozí interval 10 minut.** Portál zveřejňuje se zpožděním a automatické stahování
  v robots.txt nevítá.

### Pardubický kraj

Kraj má vlastní web s výjezdy. Integrace kombinuje jeho tři části:

- **RSS** dodá seznam výjezdů s přesným časem ohlášení, typem a popisem, u okresu
  už vyfiltrovaný,
- **přehled aktuálních výjezdů** ukazuje, které zásahy probíhají a v jakém stavu,
- **detail výjezdu** doplní podtyp, část obce, ulici a jednotky. Stahuje se jen u nových
  výjezdů, nejvýš 15 při jedné obnově, takže po prvním spuštění se podrobnosti chvíli dočítají.

Rozdíly proti ostatním krajům:

- **Jen dnešek a včerejšek.** Delší historii web v RSS nenabízí, senzor „za 7 dní“
  ukazuje tyto dva dny.
- **Bez souřadnic**, takže bez mapy a vzdálenosti.
- **U jednotek chybí typ vozidla.** Pole `technika` obsahuje jen název jednotky.

## Přechod z integrace „Zásahy JPO – Středočeský kraj“

Tato integrace nahrazuje starší [Zasahy-JPO-Stredocesky-kraj](https://github.com/LochyCZ/Zasahy-JPO-Stredocesky-kraj).

1. Odeber starou integraci a přidej tuto, kraj **Středočeský**.
2. V automatizacích přepiš eventy `pkr_zasahy_novy_zasah` na `hzs_zasahy_novy_zasah`
   a `pkr_zasahy_aktualizace_zasahu` na `hzs_zasahy_zmena_stavu`.
3. Uprav ID senzorů v kartách. Atribut se stále jmenuje `zasahy`.

## Omezení

- **Data jsou zpožděná.** Weby se obnovují zhruba jednou za minutu a dispečink události
  zveřejňuje s prodlevou.
- **Čas zásahu je čas ohlášení**, u Středočeského kraje poslední aktualizace.
  Čas vzniku události weby neuvádějí.
- **„Probíhající“ zásahy** jsou ty, které ještě nejsou ukončené, převzaté k vyřízení
  ani uzavřené.
- **Integrace závisí na neveřejných rozhraních webů.** Když se změní, senzory přejdou
  do stavu *nedostupné*. V tom případě [založ issue](https://github.com/LochyCZ/HomeAssistant-Zasahy-HZS/issues).
- **Neptej se moc často.** Servery patří krajským HZS a výchozí interval je rozumný kompromis.

## Vývoj

```bash
pip install pytest-homeassistant-custom-component
pytest tests
```

Testy běží nad vzorky skutečných dat ze všech krajů (`tests/fixtures`). Pokrývají parsery,
časové zóny, převod souřadnic, config flow, senzory, mapu i eventy.

Logování pro ladění:

```yaml
logger:
  logs:
    custom_components.hzs_zasahy: debug
```

## Upozornění

Neoficiální projekt, který nemá nic společného s HZS ČR, krajskými HZS ani krajskými úřady.
Na notifikace z této integrace nespoléhej v situacích, kde jde o bezpečnost.
Data patří jejich provozovatelům.

## Licence

[MIT](LICENSE)
