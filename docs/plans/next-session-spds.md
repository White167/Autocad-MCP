# Промт для следующей сессии: доделать этап 4 роадмапа (пакет `spds`)

Скопируй текст ниже в новую сессию Claude Code в папке
`G:\My Drive\Autocad-MCP-claude-adoring-cray-bkemrz`.

---

Ты работаешь в моём форке AutoCAD MCP Pro, ветка `claude/adoring-cray-bkemrz`.
Прочитай `CLAUDE.md`, раздел `[Unreleased]` в `CHANGELOG.md` и этап 4 в
`docs/plans/token-economy-roadmap.md`.

**Как устроен git.** Папка на Google Drive — не git-репозиторий. Коммить через
отдельный клон ветки (например, во временной папке): скопируй туда изменённые
файлы, прогони там `pytest -q -n 8` и `ruff check .`, закоммить и запушь.
Историю не переписывай.

**Живой AutoCAD.** На машине AutoCAD 2026 (25.1). Мои открытые чертежи не трогай.
В конце сделай активным тот чертёж, что был активен в начале.

## Что уже сделано (не повторять)

- Записи `ACADMCP_SPDS` (`engineering/spds/records.py`). `spds_sheet` пишет
  запись `sheet` и записи `field`. Каждый `spds_*` пишет запись `mark` на свой
  текст. `read_index(doc)` и `snapshot.read_document(backend, reader)` читают
  их за один снимок.
- `spds_sheet_update`, сводка `system_about`, исправления digest и cad_batch.
- `scripts/smoke_v17_com.py` проходит целиком на AutoCAD 2024 и 2026.

## Что осталось

1. **Шесть фокусов критики `spds_*`** (`engineering/spds/critique.py`, по
   образцу `engineering/arch/critique.py`). Индекс — один вызов
   `read_document(backend, read_index)`, общий через `shared`. Без записей
   `ACADMCP_SPDS` фокусы ничего не возвращают. Добавить в `CritiqueFocus` и
   `ALL_CRITIQUE_FOCUSES` (`engineering/plan_spec.py`), в `_FOCUS_DISPATCH`
   (`engineering/critique.py`) и в тексты `drawing_critique`/`drawing_finalize`
   в `server.py`.
   - `spds_text_height`: у текста с записью `height / scale` не из ряда
     `TEXT_HEIGHTS`. У текстов без записи на слоях СПДС проверять против
     масштабов из записей.
   - `spds_lineweight`: вес слоя СПДС отличается от `standards.LAYERS`, или вес
     объекта не из ряда ГОСТ 2.303.
   - `spds_axis_letter`: запрещённые буквы, нарушен порядок. По записям `axis`:
     `dir`/`at`/`label`; цифры слева направо, буквы снизу вверх.
   - `spds_elevation_format`: текущий текст отметки не равен
     `format_elevation(value, decimals, signed)` из записи.
   - `spds_stamp_incomplete`: пусты графы 1, 2, 4, 6, 7 записи `sheet`
     (текущие тексты полей).
   - `spds_outside_frame`: объекты листа (layout записи `sheet`) за его
     `frame`; собственный видовой экран листа уже исключён в snapshot.
   Тесты на ezdxf, плюс проверка «без записей — пусто».
2. **Форма 6 для последующих листов.** Важно: в ГОСТ Р 21.101-2020 нет формы
   2а, это термин ЕСКД. Для СПДС это форма 6, а листы основных комплектов
   чертежей идут по форме 3 с «Листов» только на первом. Размеры формы 6 даны в
   стандарте только картинкой, в библиотеке владельца их нет.
   **Сначала спроси меня**, подтверждаю ли я эти значения по своему
   экземпляру ГОСТ: 185×15; графы слева 10, 10, 10, 10, 15, 10 (Изм., Кол.уч.,
   Лист, № док., Подп., Дата); графа 1 шириной 110; графа 7 «Лист» шириной 10
   (надпись в верхних 7 мм, номер в нижних 8); три строки по 5 мм. Только после
   подтверждения: `FORM6` в `standards.py` с `SOURCE`, `spds_sheet(form=6)`,
   поддержка в `spds_sheet_update`, тесты.
3. `drawing_apply_standard("gost", scale=M)` вызывает `spds_setup`.
4. Шаблон `gost_a3_spds`: DXF через `scripts/build_templates.py` (проверка
   `--check`), `.dwt` через `scripts/smoke_settings_com.py --build-dwt` на
   AutoCAD 2026, добавить в `drawing_template_list`.
5. `scripts/smoke_spds_com.py` по критерию роадмапа: лист А3 с формой 3, сетка
   осей 4×3, три отметки и выноска — не больше 6 вызовов, без
   `system_run_lisp`. Затем `drawing_critique(focus=None)` чистый. Прогнать на
   AutoCAD 2026.
6. Прогнать на AutoCAD 2026 smoke-скрипты 1.6 (`smoke_understand_com.py`,
   `smoke_settings_com.py`, `smoke_mech_com.py`, `smoke_arch_com.py`,
   `smoke_pid_com.py`) и записать результат в CLAUDE.md и CHANGELOG.
7. Обновить таблицу статуса в `docs/plans/token-economy-roadmap.md`, скилл
   `docs/skills/autocad-spds/SKILL.md` (новые фокусы, форма 6) и архив
   `docs/skills/autocad-spds.zip`. Его же положить в
   `.claude/skills/autocad-spds/`.

В конце дай короткий отчёт: что сделано, что прошло вживую, что осталось.
