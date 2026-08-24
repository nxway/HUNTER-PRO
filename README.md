# HUNTER-PRO

Система поиска и приоритизации грузовладельцев для экспедитора. Полное ТЗ — у
владельца проекта. Всё, что ниже, — как поставить и на что смотреть при первом
запуске на реальной машине.

## Установка

Всё ставится в изолированное окружение внутри папки проекта (`.venv/`),
глобальный Python и система не трогаются. Проще всего — запустить
`HUNTER — установить.bat` (создаёт `.venv`, ставит зависимости и Chromium
для Playwright, копирует `.env.example` в `.env`). Вручную то же самое:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
copy .env.example .env
```

Остальные `.bat`-ярлыки (`собрать`, `выгрузить`, `поставить в расписание`)
сами используют `.venv\Scripts\python.exe`, а не системный `python` — руками
активировать окружение (`.venv\Scripts\activate`) для них не нужно, но можно,
если запускаете команды из README вручную в терминале.

Впишите в `.env`:
- `OPENROUTER_API_KEY` — обязателен для `ai/*` (этап 7). Без него классификация
  через ИИ выключена, всё остальное работает и без неё.
- `FSSP_API_TOKEN`, `FEDRESURS_API_TOKEN` — опциональны, нужны для `enrich/risk.py`
  (этап 5). Без токенов проверка риска просто пропускается, прогон не падает.
- `FNS_API_KEY` — пока не используется (`enrich/fns_bulk.py` работает с файлами
  открытых данных, скачанными вручную, не с платным API).

Также в `config.py` впишите свою контактную почту в `USER_AGENT` — это то, по
чему внешний сайт сможет с вами связаться, если сочтёт нужным.

## Команды

Команды ниже — с активированным окружением (`.venv\Scripts\activate` в
терминале). Без активации замените `python` на `.venv\Scripts\python.exe`.

```
python hunter.py run              # ночной цикл: собрать + обогатить + ИИ + разложить по корзинам
python hunter.py run --dry-run    # то же самое, но не тратит на ИИ и не проверяет риск — только считает
python hunter.py export           # выгрузить .xlsx новых компаний, открыть файл
```

Любой модуль запускается и сам по себе, например:
```
python -m sources.fsa_declarations --region 50 --days 30
python -m enrich.site --limit 50
python -m enrich.dgis --name "ООО Ромашка" --city "Москва"
python -m enrich.fns_bulk --load path/to/dataset.csv
python -m enrich.risk --inn 7707083893
python -m ai.classify --eval --labels my_labels.csv --models "model-a,model-b"
python inbox_import.py
python score.py
```

Два `.bat`-ярлыка на рабочий стол — `HUNTER — собрать.bat` (`run`) и
`HUNTER — выгрузить.bat` (`export`). `HUNTER — поставить в расписание.bat`
регистрирует ночной запуск в планировщике Windows на 02:00.

## Логи и данные

- `logs/hunter.log` — файл лога с ротацией по дням, обязательно смотрите сюда
  после ночного прогона.
- `leads.sqlite` — база, не в git. Бэкапьте раз в неделю на внешний диск.
- `raw/` — сырые ответы источников (gzip), не в git.
- `exports/` — готовые `.xlsx`, не в git.
- `inbox/` — сюда кладёте заполненный после обзвона файл, `hunter.py run`
  подхватит его сам и перенесёт в `inbox/processed/`.

## Зоны риска — не проверено вживую

Часть кода написана по документированному поведению источников, но не
проверена на реальном ответе сервера: облачная песочница, где это писалось,
блокирует все внешние сайты на уровне сети. Каждый такой файл сам себя
помечает докстрингом "СТАТУС ПРОВЕРКИ" — сводка:

| файл | что проверить | как |
|---|---|---|
| `sources/fsa_declarations.py` | `SEARCH_URL`, `_build_params()`, `_parse_results()` | открыть pub.fsa.gov.ru/rds/declaration, DevTools → Network, сверить реальный запрос |
| `enrich/site.py` | `_parse_search_results()` (поиск через DuckDuckGo) | сделать поисковый запрос вручную, сверить разметку результата |
| `enrich/dgis.py` | адрес поиска и селекторы карточки организации | открыть 2gis.ru, найти организацию, Inspect на телефоне/адресе/сайте |
| `enrich/risk.py` | `FSSP_URL`, `FEDRESURS_URL` и разбор ответа | получить токены у ФССП/Федресурс, свериться с их документацией |
| `enrich/fns_bulk.py` | `_COLUMN_ALIASES` (реальные заголовки колонок) | скачать набор данных ФНС, прислать первые 5-10 строк |
| `ai/client.py` | не структура (она стандартная, OpenAI-совместимая), а сам ключ и выбор модели | вписать `OPENROUTER_API_KEY`, прогнать `ai/classify.py --eval` |

Всё остальное (`config.py`, `db.py`, `score.py`, `export.py`, `inbox_import.py`,
`hunter.py`, `sources/registry.py`, `sources/base.py`, `enrich/inn.py`) проверено
на живых данных офлайн и не требует сверки — эти части не ходят в сеть вообще.
