# PARI Basketball AI

## Серверный детектор и Telegram «Сигнал»

Сборщик рассчитывает верхний детектор примерно каждые 10 секунд без браузера: `server_detector.py` выполняет те же функции `static/app.js` и `static/forecast-core.js` во встроенном V8. Интерфейс получает серверный список через LIVE API. Правила отбора и модельные вероятности не меняются.

В закрытых переменных существующего Render-сервиса задаются `SIGNAL_SERVER_ENABLED=1`, `SIGNAL_BOT_TOKEN`, `SIGNAL_CHAT_ID`. Токен не входит в исходники, ответы API или журналы. `/api/signals/status` показывает время расчёта, свежесть и состояние обработки команд. Сообщения доступны только заданному владельцу; `/status`, `/pause`, `/resume` обрабатываются на сервере. GitHub-проверка при включённом серверном режиме лишь обращается к его API и не запускает второго Telegram-поллера.

Render Free может засыпать и перезапускаться; GitHub-проверки примерно раз в пять минут не гарантируют постоянную доступность. История отправок и пауза находятся на временном диске, поэтому могут сброситься. После сброса свежие карточки отправляются; прежняя история повторов может потеряться. Непрерывная работа 24/7 на бесплатном тарифе не гарантируется.

Обработчик SofaScore принимает объект статистики и массив периодов с `statisticsItems`, сохраняет границы четвертей и отличает недоступный источник от пустого LIVE-списка. Это не создаёт статистику для лиг, которые провайдер не покрывает.

Веб-приложение для read-only анализа live-баскетбола и тоталов. Прогнозы виртуальные: приложение не размещает ставки и не проводит денежные операции.

## Что обслуживает опубликованный сайт

Render запускает `uvicorn server:app --host 0.0.0.0 --port $PORT`.

- Backend: корневой `server.py`.
- Главная страница: `static/index.html` (маршрут `GET /` читает именно этот файл).
- Рабочий frontend: `static/app.js`, `static/forecast-core.js`, `static/styles.css`.
- Корневые `app.js`, `index.html`, `styles.css` — старый MVP; опубликованный маршрут их не подключает.
- Python-модули из `static` не используются сервером. Не редактируйте корневой MVP вместо файлов `static/`.

## Основной прогноз

- Один основной результат передаётся в основную карточку, AI-подписанный блок и панель сводки. AI-подписанный блок — повтор основного расчёта, а не отдельная обученная ИИ-модель.
- Для полного матча базовая оценка строится из текущего счёта, подтверждённого времени и формата. При проверенных парных показателях владений применяется отдельная поправка модели владений.
- Распределение полного матча моделируется 100 000 сценариями. Монте-Карло — способ вычисления распределения, а не самостоятельная независимая модель.
- Тотал текущего периода рассчитывается через Gamma-Poisson posterior predictive. Prior берётся из очков и игрового времени завершённых периодов этого же матча; текущий период обновляет оценку по фактически прошедшим секундам. Предсказательная дисперсия включает неопределённость темпа.
- В первые три минуты без завершённых периодов четвертный прогноз не выдаётся. Если prior слабый, прогноз помечается как предварительный.
- Целые линии отдельно учитывают возврат; для половинчатых линий вероятность возврата равна нулю.
- Интервалы 80%/95% — прогнозные интервалы итогового тотала. Неопределённость среднего ожидаемого тотала выводится отдельно (для четверти — posterior rate uncertainty, для матча — разброс средних по сценариям).
- Вероятности пока не калиброваны на независимой размеченной выборке. Они являются модельными оценками, не гарантией исхода.

## Что не считается готовой моделью

- Старая «Байесовская» смесь исключена: она смешивала полный тотал матча со средним тоталом четверти, то есть величины с разными размерностями.
- Старый «Динамическая регрессия» исключён из ансамбля: это было среднее коррелированных оценок без обученных коэффициентов.
- Монте-Карло не считается отдельной моделью.
- Импульс ускорения/спада влияет один раз на веса сценариев Монте-Карло, не сдвигая средний прогноз повторным множителем. При наличии согласованных снимков одного периода он измеряется по фактической разнице счёта и игрового времени за 3–5 минут; при нехватке истории явно используется периодный fallback. Источник импульса записывается в контракт.
- Снимки для короткого окна читаются из локальной SQLite Render. После холодного старта/сброса диска требуется накопить не менее 3 минут истории; до этого rolling-показатель недоступен, и система не выдумывает его. Сама смесь сценариев экспериментальная и не калибрована: momentum складывается из 55% динамики последнего периода, 25% сравнения последнего периода с более ранними и 20% темпа текущего периода; pFast=clamp(0.25+0.55×momentum, 0.12–0.40), pSlow=clamp(0.25−0.25×momentum, 0.16–0.34), остаток — базовый сценарий. Это не обученные веса и не подтверждённое преимущество. В четверти текущий темп обновляет Gamma-Poisson posterior; дополнительная поправка не применяется повторно.
- Smooth Game — диагностический коэффициент траектории; модель концовки — ограниченная эвристика. Ни один из них не считается независимым голосом ансамбля.
- Марковская цепь не активна: нет проверенной матрицы переходов владений из play-by-play.
- CatBoost/LightGBM и калибровка не обучены: для них нужны подтверждённые финальные исходы и временное разделение выборки.
- Фильтр Калмана и эвристики темпа не считаются независимыми подтверждениями.

## Формат, рынок и качество данных

- Время хранится и рассчитывается в секундах; формат матча должен быть подтверждён или явно помечен как предварительный.
- NCAA 2×20 не трактуется как четыре четверти. Для 20-минутных периодов нормализация выставляет два периода.
- Основной прогноз полного матча сейчас оценивает основное время; ОТ не включён, а правило линии PARI по ОТ не подтверждено. Поэтому направление относительно линии не следует трактовать как проверенное преимущество, пока это правило не получено.
- Если событие уже в ОТ, прогноз блокируется до появления проверенной модели длительности ОТ и правил рынка.
- Отсутствующие показатели остаются отсутствующими. Режимы качества разделены на: только счёт/время; счёт/время/результаты периодов; частичная статистика и владения; полный play-by-play. Источник счёта, таймера, статистики и возраст ответа API показываются отдельно; время обновления самого события помечается неизвестным, если провайдер его не передал.
- Сопоставление внешней статистики требует совпадения обеих команд и совместимости лиги/соревнования, даты, гендерной и возрастной категории и длительности периода; неоднозначные или несовместимые кандидаты отклоняются. LPBL и Cater исключаются.
- Сопоставление коэффициентов сохраняет исходные фактор-ID и цены. Стороны ТБ/ТМ не назначаются без подтверждённой расшифровки фактора.
- Время ответа API и время обновления самого события — разные вещи. Если источник не передаёт время обновления события, это отмечается как неизвестное.

## История и архив

Настройка обновлённого GitHub-сборщика, проверка публикации и восстановление: [ARCHIVE_SETUP.md](ARCHIVE_SETUP.md). Новые ZIP имеют manifest v3; отдельный проверенный inventory предотвращает повторную публикацию артефактов. Отложенные снимки подбираются без ограничения последними 24 часами, пока они доступны в Actions.

- История карточки в браузере — локальная копия последних 500 снимков; она не является постоянным серверным хранилищем. В каждой записи исход остаётся «не подтверждён» до независимой сверки финального счёта.
- Render Free не имеет постоянного диска. Локальная SQLite на `/tmp` теряется при перезапуске/переразмещении; её нельзя называть долговременной базой.
- Дополнительный Render-сборщик опрашивает PARI примерно раз в 10 секунд, пока процесс сервиса работает и не спит.
- GitHub Actions отдельно собирает снимки LIVE и расчёты каждые 5 минут. Успешные артефакты сворачиваются в ежедневные ZIP-релизы GitHub Releases. Это постоянная архивная копия, не транзакционная БД и не гарантия сбора во время недоступности источника/Actions.
- Архив содержит снимки и версии прогнозов. Подтверждённая автоматическая сверка финального результата по всем лигам пока не реализована; исход без независимого финального подтверждения должен оставаться unresolved.
- Никакие существующие архивы и данные не удаляются при обычном обновлении кода. Очистка локальной SQLite — отдельная операция пользователя.

## Проверка

- Python: `python -m unittest discover -s tests -v`.
- JavaScript forecast regressions: `node tests/forecast-core.test.cjs`.
- GitHub Actions запускает оба набора тестов на push и pull request.

## Ручные расчётные API

- `POST /api/analyze` поддерживает явные `period_count` и `period_minutes`; `clock` здесь задаётся как прошедшее время периода. Для 2×20 нужно передать `period_count: 2`, `period_minutes: 20`.
- `POST /api/analyze-quarter` без истории предыдущих периодов не выдаёт прогноз в первые 3 минуты; затем использует Gamma-Poisson predictive distribution. Это вспомогательный ручной API, не отдельный источник основного LIVE-прогноза сайта.

## Диагностические маршруты

- `GET /api/health` — готовность приложения.
- `GET /api/pari/live` — PARI LIVE, нормализованные матчи и диагностика источников.
- `GET /api/pari/diagnostics` — фильтрация LIVE.
- `GET /api/pari/match-debug?team=...` — структура матча.
- `GET /api/pari/source-check` — доступность публичной страницы PARI.
- `GET /api/pari/discover` — исследование публичных маршрутов PARI.
- `GET /api/archive/status` — локальная SQLite и статус Render-сборщика.
- `GET /api/archive/export` — ZIP текущей локальной SQLite; это не замена постоянному GitHub-архиву.
- Восстановление ZIP без удаления/перезаписи: `python .github/scripts/restore_daily_archive.py <archive.zip> --output pari-archive-restored`. Скрипт проверяет ZIP CRC, gzip/JSON и SHA-256 файлов из manifest; конфликтующие существующие файлы не перезаписывает.
- `POST /api/archive/settle-preview` — проверяет предоставленный финальный результат, но принимает его только при точном ID матча, подтверждённой идентичности, независимом подтверждении, зафиксированной линии и известном правиле ОТ. Это preview, не автоматический сбор финалов и не долговременное сохранение исхода.

## Запуск

```bash
pip install -r requirements.txt
uvicorn server:app --reload
```

Production: https://pari-basketball-ai.onrender.com/

## Качество прогнозов и журнал исследований (v2)

Текущие модели матча и четверти оценивают основное время. Модель овертайма не обучена:
для рынка с ОТ такой расчёт остаётся `shadow`, а не выдаётся за совместимый прогноз.
Вероятности не калиброваны. Порог 65% сам по себе не даёт права на уверенный сигнал.
Автоматическое выделение/доставка уверенных сигналов отключены до независимой калибровки
и проверки преимущества. Расчёты, интервалы и инструменты остаются в разборе матча.
Это изменение допуска сигналов, не заявление о повышении точности.

Статусы: `shadow` — критические данные/правила рынка не подтверждены;
`research` — данные и рынок совместимы, но преимущество не доказано.
Текущая версия не создаёт `formal_edge`. Флаги из фида или браузера не включают этот режим.
Для его реализации нужен отдельный проверяемый артефакт калибровки и теста той же версии,
лиги, формата и цели; обучение, калибровка и финальный тест разделяются по целым матчам и времени.
Рост выборки до 100 матчей сам по себе не доказывает преимущество.

Для каждой линии `verified_total_markets` задаётся адаптером только после проверки:
`eventId`, `target` (`match`/`quarter`), `period` (для четверти), `line`,
`rulesConfirmed`, `ruleSource`, `includesOvertime`, `pricesVerified`,
`overOdds`, `underOdds`, `quotedAtUtc`, `suspended`.
Принадлежность фактора ТБ/ТМ нельзя угадывать по его порядку или цене.
Существующий PARI-фид пока не подтверждает все эти поля: это честный блокер, не нулевой коэффициент.
Из обеих подтверждённых цен считаются маржа, no-vig и **модельная**, не доказанная доходность.
Возврат учитывается отдельно: `pWin × odds + pPush − 1`.
Срок пригодности ответа и котировки — до 90 секунд, но он не доказывает отсутствие задержки провайдера.

Сервер сохраняет все оценённые линии, причины отказа, исходный снимок, прогноз и SHA-256
кода в append-only SQLite. `FORECAST_JOURNAL_PATH` должен указывать на постоянный диск;
по умолчанию используется временный каталог Render. GitHub-снимки также содержат
`signal_evaluations`; сбор через GitHub идёт отдельно и реже, поэтому не является точной копией 30-секундного журнала.
Публичные маршруты только читают: `/api/research/status`, `/api/research/export` (JSONL).
Секреты в журнал не передаются. Журнал не проводит ставки.

```bash
python research_journal_cli.py status
python research_journal_cli.py export forecasts.jsonl
python research_journal_cli.py confirm FORECAST_ID independently_verified_final.json
python evaluate_forecasts.py /path/to/pari_forecasts.sqlite3 --output evaluation.json
node scripts/replay_forecasts.cjs historical_inputs.json replay.json
```

Финал принимается только с точным event ID, независимым подтверждением, источником,
первоначальной линией и теми же правилами ОТ, которые были сохранены **до** результата.
JSON финала использует контракт `result_validation.py`: `final_status`, `confirmed`,
`identity_verified`, `independent_confirmation`, `source`, `includes_overtime`,
`market_rule_confirmed`, `line`, `regulation_total`/`total_with_overtime` либо
`period_number`/`period_total`, `confirmed_at_utc`.
Автоматический независимый сбор финалов всех лиг пока не подключён; отсутствие финала остаётся unresolved.
Изменённый подтверждённый результат не перезаписывает старый: конфликт требует расследования.

Подтверждённый счёт основного времени сохраняется отдельно в `labels` даже при неизвестном
правиле рынка: его можно использовать для MAE/Brier, но нельзя считать выигрышем ставки.
`confirmed_games` считает независимые матчи с такой сверкой, а `confirmed` — рассчитанные
рыночные исходы. Экспорт JSONL содержит обе записи; версии модели в отчёте не объединяются.

Оценка использует первый пригодный снимок на матч/цель/четверть и ближайшую к прогнозному
центру линию, выбранную без результата. Поздний удачный снимок не заменяет первый без финала.
Разделение 70/30 хронологическое, по целым матчам. Отдельно выводятся цели, MAE/bias
основного времени, покрытие интервала, Brier, темповый baseline, рынок с той же целью и
виртуальный ROI каждого класса. Это диагностический отчёт: он не обучает калибратор,
не оценивает неопределённость ROI и не разрешает автоматическое включение сигналов.


### Численная точность и наблюдение (v3, 22.11.2026)

Четвертная Gamma-Poisson модель теперь вычисляется напрямую через отрицательное биномиальное распределение: без случайной симуляции и нормального приближения. Центр модели не переобучен. Это устраняет численную погрешность, но не доказывает улучшение точности на матчах. Проверяется остаток массы распределения; некорректный расчёт блокируется. Версия модели изменена, результаты v2 и v3 не объединяются автоматически.

Отдельная синяя панель содержит только **исследовательские ориентиры**. До просмотра вероятностей выбирается центральная доступная линия; используются прежние окна (матч Q4 после 2:00, четверть 4–8 минута). Направление должно сохраняться и иметь минимум 65% в каждом доступном варианте оценки темпа: вся история, последний завершённый период и текущий период. Для четверти нужны все три варианта; для матча Q4 — основной расчёт и минимум две оценки остатка Q4. Это сравнение зависимых предположений на одном матче, не независимые голоса, калибровка или доверительный интервал. Проверенные цены дополнительно должны давать положительную модельную доходность при худшем из вариантов. При неизвестных правилах/ценах выгода не заявляется.

Ориентиры не поступают в `priority_signals`, не отправляются как уверенные сигналы и не повышают статус до `formal_edge`. Время жизни — 90 секунд. LIVE API и `/api/signals/status` содержат `research_candidates` и `signal_diagnostics`/`diagnostics`: причины отказа, число линий и недоступные расчёты. Это показатели текущего снимка, не статистика за день. Журнал сохраняет проверку чувствительности каждой линии.

Добавлены точные PARI ID форматов для Serie A/A2, Pro A, PLK и Basketligan по правилам соответствующих федераций; неизвестные и молодёжные форматы не наследуют длительность по стране. Доказательства формата находятся в `PARI_COMPETITION_FORMATS`, арифметика источника и таймер проверяются отдельно. Официальные правила: [FIP](https://fip.it/regolamenti/), [FFBB](https://www.ffbb.com/reglement-de-jeu), [PZKosz](https://szkolenie.pzkosz.pl/dokumenty/pobierz/4002/Oficjalne_Przepisy_Gry_FIBA_2024.pdf), [SBBF](https://www.basket.se/aktiva/domare/regler/).


### Получение данных PARI (v4, 22.11.2026)

Общий `/events/list` содержит лишь часть факторов. Сборщик дополнительно использует публичные `/events/event?eventId=...` и `/sportscast/actrans?lang=multi`, как PARI 2.13.96. Формат читается из `extra.quarters` только при точном совпадении event ID и обеих команд и basketball sport_code; общее значение 4×10 не подставляется. Полная карточка заменяет весь подграф матча (счёт/время/факторы/блокировки), а не только новые цены. До 24 карточек за цикл, 4 параллельных запроса и общий бюджет 7 секунд; неудача сохраняет базовый ответ. Форматные метаданные кэшируются до 60 секунд, карточки не переносятся на свежий список из старого кэша.

`pari_total_catalog.json` — компактная проверенная выдержка LIVE-конфигурации баскетбола, таблиц 18319/2673. Стороны 930/931 и альтернативных пар подтверждены заголовками «Больше»/«Меньше», а не порядком цен. Сохраняются sourceEventId, factor IDs, версия/ссылка справочника, обе цены одной линии и блокировки root/child. `quotedAtUtc` — момент получения ответа, **не** известная дата последнего изменения котировки провайдером; `quoteTimestampKind` это явно указывает. Неизвестные и конфликтующие факторы не сопоставляются.

Правила PARI 8.1 подтверждают учёт ОТ для тотала матча. Модель по-прежнему считает основное время: ценам даётся корректный статус, но EV для несовместимого рынка не выводится и формальный допуск не включается. Тотал конкретной четверти определяется точным eventKind 100401–100404, без примеси командных статистических подрынков. Наличие «Показателей команд» не означает, что источник передаёт FGA/ORB/TO: отсутствующие поля не заполняются вымышленными значениями. Диагностика подключения находится в `pari_data_diagnostics` каждого события.

### Input reliability and model readiness (2026-11-23)
- Reject missing, negative and fractional scores rather than converting them to zero; reject malformed prior periods and future-dated snapshots.
- Retry the secondary PARI feed on request failures. Cache only event/team-scoped static format evidence for at most 24 hours, never cached live scores or prices.
- Reject malformed or score-regressing detail cards without replacing the valid base snapshot.
- Separate input, model and selection blockers in the signal panel and expose research readiness in `/api/research/status`.
- Evaluate full-match tempo using the actual period count (including 2x20); omit this baseline when format is unknown.
- Confident promotion still requires independently verified finals, trained calibration and untouched later-game evaluation. Full-match PARI totals include overtime; the regulation model is not an overtime model. Persistent journal storage and automatic independent final verification are not configured by this patch.

### Signal audit repair (2026-11-23)
- Notification identity includes event + target + period + line + direction. Exact repeats are suppressed; changed lines/directions are explicitly marked as updates to the same observation, not new entries. Fresh first-start cards are delivered rather than silently marked sent.
- A notification requires the immutable forecast ID created before delivery. Attempt/success/failure events and Telegram message references are append-only and exported with the original decision, source snapshot, both market prices and model source hash.
- `/api/research/export?deliveries_only=true` exports only attempted delivery decisions. The existing five-minute GitHub collector includes this journal in retained artifacts/daily archives, with explicit availability status. The capture interval and Render ephemeral resets can still leave a gap; this is not a durable exactly-once queue or a replacement for persistent storage.
- Quarter sensitivity now includes mathematically specified scoring events worth 2 and 3 points (Gamma-Poisson count of marked events; mean preserved, variance = mark*mean + rate uncertainty), and a return to the highest already observed completed-period rate with three-point events. No loss-fitted variance multiplier or future score is used. Duplicate baseline scenarios are removed. These are stress scenarios, not independent trained models or calibrated win probabilities.
- Navigation displays the envelope of per-scenario 95% model ranges as an uncalibrated scenario corridor, with no claimed joint coverage. Telegram experimental messages omit the misleading percentage. Original distributions and model numbers remain available for evaluation.
- The fourteen exact archived inputs from the audit are regression fixtures, not a held-out performance test. Neither accuracy improvement nor profitability is established; full-match overtime compatibility and independent validation gates remain in force.

### PARI match-centre statistics and women's games (2026-11-23)
- Women's basketball is included in discovery and optional provider lists. Gender markers (including WCBA/WKBL and short team tags) remain part of identity checks; women's rows are visibly marked and carry an unvalidated league-scope note. Esports/virtual/3x3 exclusions remain.
- `/sportscast/events` is now requested for exact PARI event/team/broadcast identities with a confirmed regular basketball format. The bounded collector tries at most two broadcasts per event and does not let a failed centre break scores or markets.
- `pari_matchcentre.py` removes canceled events, deduplicates IDs, handles explicit team reversal and scoring corrections, and reconciles reconstructed points against both the root and each period. Only a reconciled source contributes named made-shot, foul and explicitly observed event counters. Possession changes are not possessions; assigned free throws are not completed attempts. FGA/FTA and ORB/DRB are not fabricated from incomplete event coverage.
- The UI and API expose each centre's state and source URL, period-specific metric coverage and missing possession-model inputs. Statistics now inherit the synchronized PARI snapshot time, with a distinct observation basis rather than a false independent provider timestamp. Duplicate FGM derivation is removed.
- These changes restore source coverage, not calibrated win probabilities. No automatic confidence promotion or cross-gender transfer of learned weights is introduced.

### Notification delivery repair (2026-11-23)
- Refresh the collector every 10 seconds while Render is awake. A separate sender retries failed cards every 3 seconds without blocking collection or bot commands. One failed card does not suppress its peers; disappeared or expired candidates are not retried.
- With the explicit experimental opt-in, mirror all eligible research candidates from the website. Market compatibility is stated in each message; regulation forecasts for overtime markets are observation-only and must not be applied to that market. Confirmed-priority and model-validation gates remain unchanged.
- The status API exposes delivery decisions per event/target/period/line and the last send time, so already-sent, failed/retrying, and expired notifications can be distinguished. Ephemeral free hosting still cannot guarantee durable exactly-once delivery or continuous uptime.
