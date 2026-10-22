"""СПДС values: sheet formats, the form 3 title block, lines, lettering, axes, levels.

SOURCE
------
Every number below was transcribed from the AutoLISP library of the
``autocad-spds`` skill that the owner of this fork drafts with (``spds:*``,
"проверено на AutoCAD 2024 (рус.)"), whose own references are the standards
named per block. The standards were **not** re-read for this module: where the
skill states a value it is copied, where the standard allows a range the skill's
choice is kept and the range is named. A value outside what is transcribed is
refused by name - nothing is interpolated or invented.

* Formats ............ ГОСТ 2.301-68, табл. 1 (А4 210 x 297, А3 420 x 297).
* Frame .............. ГОСТ Р 21.101-2020 / ГОСТ 2.301: 20 mm filing margin
                       on the left, 5 mm on the other three sides.
* Основная надпись ... ГОСТ Р 21.101-2020, приложение Ж, форма 3: 185 x 55 mm,
                       graphs 1-19 as the skill numbers them.
* Lines .............. ГОСТ 2.303-68: S = 0.5 mm; thin S/3-S/2 (0.18 here);
                       dash 2-8 / gap 1-2 (4 / 1.5 here); dash-dot 5-30 /
                       gap 3-5 (15 / 2 x 2 here).
* Lettering .......... ГОСТ 2.304-81: heights 2.5, 3.5, 5, 7, 10, 14, 20 mm;
                       ``isocpeur.ttf`` (shipped with AutoCAD) as the type A
                       stand-in the owner chose.
* Dimensions ......... ГОСТ Р 21.101-2020 п. 5.4.2 / ГОСТ 2.307-2011: 45° ticks
                       2-4 mm, the dimension line 0-3 mm past the extension
                       lines, numbers in mm, decimal comma.
* Levels ............. ГОСТ Р 21.101-2020 п. 5.4.3: metres, three decimals,
                       comma; zero unsigned, "+" above, "-" below; on plans in
                       a rectangle.
* Coordinate axes .... ГОСТ Р 21.101-2020: dash-dot thin line, mark in a
                       circle of 6-12 mm; digits on the side with more axes,
                       Russian capitals on the other without Ё З Й О Х Ц Ч Щ
                       Ъ Ы Ь; left to right, bottom to top.

Layer names, colours and the per-scale dimension style name ``СПДС_1-M`` are
the skill's conventions, not standard values.
"""

from __future__ import annotations

__all__ = [
    "AXIS_CIRCLE_RANGE",
    "AXIS_LETTERS",
    "AXIS_MARK_DIAMETER",
    "AXIS_MARK_HEIGHT",
    "DIMSTYLE_PREFIX",
    "FORBIDDEN_AXIS_LETTERS",
    "FORM3",
    "FORMATS",
    "FRAME_MARGINS",
    "GOST_LINEWEIGHTS",
    "LAYERS",
    "LINETYPES",
    "TEXT_FONT",
    "TEXT_HEIGHTS",
    "TEXT_STYLE",
    "dimstyle_name",
    "dimstyle_values",
    "sheet_size",
]

# ── formats and frame ───────────────────────────────────────────────────────

#: (format, orientation) -> (width, height) mm. А4 only portrait: the title
#: block runs along the short side (ГОСТ 2.301; the skill's §2.1).
FORMATS: dict[tuple[str, str], tuple[float, float]] = {
    ("A4", "portrait"): (210.0, 297.0),
    ("A3", "landscape"): (420.0, 297.0),
    ("A3", "portrait"): (297.0, 420.0),
}

#: left (filing), right, bottom, top - mm.
FRAME_MARGINS = {"left": 20.0, "right": 5.0, "bottom": 5.0, "top": 5.0}


def _format_key(fmt: str) -> str:
    key = str(fmt).strip().upper().replace("А", "A")  # Cyrillic А accepted
    return key


def sheet_size(fmt: str, orientation: str | None = None) -> tuple[float, float, str, str]:
    """(width, height, format, orientation); refuses what is not transcribed."""
    key = _format_key(fmt)
    if orientation is None:
        orientation = "portrait" if key == "A4" else "landscape"
    orient = str(orientation).strip().lower()
    if (key, orient) not in FORMATS:
        have = ", ".join(f"{f} {o}" for f, o in FORMATS)
        raise ValueError(f"format {fmt!r} {orientation!r} is not transcribed; have: {have}")
    w, h = FORMATS[(key, orient)]
    return w, h, key, orient


# ── form 3 ──────────────────────────────────────────────────────────────────

#: The основная надпись форма 3, lower-left corner at (0, 0), mm.
#: ``thick`` / ``thin`` are (x1, y1, x2, y2) rules; ``headers`` the printed
#: column names; ``fields`` graph number -> (centre x, centre y, height, max
#: width, may wrap to two lines); ``signature_rows`` the five lines of the
#: Разраб. / Пров. ... block (role, surname, date column x positions).
FORM3 = {
    "width": 185.0,
    "height": 55.0,
    "thick": (
        (0, 0, 185, 0),
        (185, 0, 185, 55),
        (185, 55, 0, 55),
        (0, 55, 0, 0),
        (65, 0, 65, 55),
        (20, 0, 20, 55),
        (40, 0, 40, 55),
        (55, 0, 55, 55),
        (10, 25, 10, 55),
        (30, 25, 30, 55),
        (135, 0, 135, 30),
        (150, 15, 150, 30),
        (165, 15, 165, 30),
        (0, 25, 65, 25),
        (0, 30, 185, 30),
        (65, 15, 185, 15),
        (65, 45, 185, 45),
    ),
    "thin": (
        (0, 5, 65, 5),
        (0, 10, 65, 10),
        (0, 15, 65, 15),
        (0, 20, 65, 20),
        (0, 35, 65, 35),
        (0, 40, 65, 40),
        (0, 45, 65, 45),
        (0, 50, 65, 50),
        (135, 25, 185, 25),
    ),
    # (text, centre x, centre y, max width)
    "headers": (
        ("Изм.", 5.0, 27.5, 8.5),
        ("Кол.уч", 15.0, 27.5, 8.5),
        ("Лист", 25.0, 27.5, 8.5),
        ("№ док.", 35.0, 27.5, 8.5),
        ("Подп.", 47.5, 27.5, 13.0),
        ("Дата", 60.0, 27.5, 8.5),
        ("Стадия", 142.5, 27.5, 13.0),
        ("Лист", 157.5, 27.5, 13.0),
        ("Листов", 175.0, 27.5, 18.0),
    ),
    "header_height": 2.5,
    # graph -> (cx, cy, height, max width, wrap)
    "fields": {
        1: (125.0, 50.0, 5.0, 115.0, False),  # обозначение документа (шифр)
        2: (125.0, 37.5, 3.5, 115.0, True),  # наименование объекта / предприятия
        3: (100.0, 22.5, 3.5, 67.0, True),  # наименование здания
        4: (100.0, 7.5, 3.5, 67.0, True),  # наименование изображений на листе
        6: (142.5, 20.0, 3.5, 13.0, False),  # стадия
        7: (157.5, 20.0, 3.5, 13.0, False),  # лист
        8: (175.0, 20.0, 3.5, 18.0, False),  # листов (на первом листе)
        9: (160.0, 7.5, 3.5, 47.0, True),  # организация
    },
    # five rows, top first: role (left, x 1), surname (left, x 21), date (centre, x 60)
    "signature_rows": 5,
    "signature_top_y": 22.5,
    "signature_step": 5.0,
    "signature_columns": ((1.0, 18.0, "L"), (21.0, 18.0, "L"), (60.0, 9.0, "C")),
    "signature_height": 2.5,
    # the corner of the frame the block sits in: 5 mm from the right and the
    # bottom edge of the sheet, i.e. against the inner frame
    "offset_from_sheet_right": 190.0,
}

#: Graph names, for the tool's `fields` keys besides the numbers.
FORM3_FIELD_NAMES = {
    "code": 1,
    "project": 2,
    "building": 3,
    "sheet_title": 4,
    "stage": 6,
    "sheet": 7,
    "sheets": 8,
    "organisation": 9,
}

#: Additional graphs in the 20 mm margin (Инв. № подл. 25, Подп. и дата 35,
#: Взам. инв. № 25 - bottom up), the skill's `inv` option.
EXTRA_COLUMNS = {
    "thick": (
        (8, 5, 8, 90),
        (13, 5, 13, 90),
        (8, 5, 20, 5),
        (8, 30, 20, 30),
        (8, 65, 20, 65),
        (8, 90, 20, 90),
    ),
    "labels": (("Инв. № подл.", 17.5), ("Подп. и дата", 47.5), ("Взам. инв. №", 77.5)),
    "label_x": 10.5,
    "label_height": 2.5,
    "label_width_factor": 0.8,
}

#: "Формат А3" under the frame, right: centre (sheet width - 30, 2.5), 2.5 mm.
FORMAT_LABEL = {"from_right": 30.0, "y": 2.5, "height": 2.5}

# ── lines, layers, lettering ────────────────────────────────────────────────

#: name -> (pattern in mm of paper, description). .lin convention.
LINETYPES: dict[str, tuple[tuple[float, ...], str]] = {
    "GOST_DASH": ((4.0, -1.5), "GOST 2.303 dashed"),
    "GOST_AXIS": ((15.0, -2.0, 0.0, -2.0), "GOST 2.303 dash-dot"),
    "GOST_2DOT": ((15.0, -1.5, 0.0, -1.5, 0.0, -1.5), "GOST 2.303 dash-dot-dot"),
}

#: name -> (ACI colour, linetype, lineweight mm). The skill's set, plus the two
#: sheet layers this pack draws on.
LAYERS: dict[str, tuple[int, str, float]] = {
    "СПДС_Рамка": (7, "Continuous", 0.50),
    "СПДС_Рамка_тонкие": (7, "Continuous", 0.18),
    "Контур": (7, "Continuous", 0.50),
    "Тонкие": (7, "Continuous", 0.18),
    "Невидимые": (7, "GOST_DASH", 0.25),
    "Оси": (1, "GOST_AXIS", 0.18),
    "Оси_марки": (1, "Continuous", 0.18),
    "Размеры": (3, "Continuous", 0.18),
    "Отметки": (3, "Continuous", 0.18),
    "Выноски": (3, "Continuous", 0.18),
    "Текст": (7, "Continuous", 0.18),
    "Штриховка": (8, "Continuous", 0.18),
}

#: The AutoCAD lineweights inside ГОСТ 2.303's range: the thin line from S/3 of
#: the thinnest S (0.5 -> 0.17, AutoCAD's 0.18) to the thickest S (1.4). The
#: standard gives ranges, not a series; this is the AutoCAD set clipped to them
#: (the ``spds_lineweight`` focus judges an entity's own weight against it).
GOST_LINEWEIGHTS: tuple[float, ...] = (0.18, 0.25, 0.35, 0.50, 0.70, 1.00, 1.40)

TEXT_STYLE = "GOST"
TEXT_FONT = "isocpeur.ttf"
TEXT_HEIGHTS: tuple[float, ...] = (2.5, 3.5, 5.0, 7.0, 10.0, 14.0, 20.0)

# ── dimension styles ────────────────────────────────────────────────────────

DIMSTYLE_PREFIX = "СПДС_1-"


def dimstyle_name(scale: int) -> str:
    return f"{DIMSTYLE_PREFIX}{int(scale)}"


def dimstyle_values(scale: float) -> dict:
    """The skill's ``spds:dimvars`` for 1:``scale``, as whitelist variables.

    Ticks ``_ARCHTICK`` 2 mm; the dimension line 2 mm past the extension
    lines; extension lines 2 mm past the dimension line and starting at the
    feature; text 2.5 mm, 1 mm above the line, centred; whole millimetres with
    a decimal comma. DIMSCALE carries the drawing scale (model space 1:M).
    """
    if scale <= 0:
        raise ValueError(f"scale must be positive; got {scale!r}")
    return {
        "DIMSCALE": float(scale),
        "DIMTXSTY": TEXT_STYLE,
        "DIMTXT": 2.5,
        "DIMBLK": "_ARCHTICK",
        "DIMASZ": 2.0,
        "DIMDLE": 2.0,
        "DIMEXE": 2.0,
        "DIMEXO": 0.0,
        "DIMGAP": 1.0,
        "DIMTAD": 1,
        "DIMJUST": 0,
        "DIMTIH": 0,
        "DIMTOH": 0,
        "DIMTOFL": 1,
        "DIMATFIT": 3,
        "DIMTMOVE": 2,
        "DIMLUNIT": 2,
        "DIMDEC": 0,
        "DIMDSEP": ",",
        "DIMZIN": 8,
        "DIMLFAC": 1.0,
        "DIMCLRD": 256,
        "DIMCLRE": 256,
        "DIMCLRT": 256,
        "DIMLWD": -2,
        "DIMLWE": -2,
    }


# ── axes and levels ─────────────────────────────────────────────────────────

AXIS_MARK_DIAMETER = 8.0
AXIS_CIRCLE_RANGE = (6.0, 12.0)
AXIS_MARK_HEIGHT = 5.0
FORBIDDEN_AXIS_LETTERS = frozenset("ЁЗЙОХЦЧЩЪЫЬ")
#: The Russian capitals left for axis marks, in order.
AXIS_LETTERS: tuple[str, ...] = tuple(
    ch for ch in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ" if ch not in FORBIDDEN_AXIS_LETTERS
)

#: The level sign (skill's spds:elev, mm of paper): two 45° strokes from the
#: level point, 2.1 mm each way; the stem 6 mm; text 2.5 mm, 0.8 above the
#: shelf, 0.75 in from its start; the shelf 1.5 mm longer than the text.
ELEVATION = {
    "stroke": 2.1,
    "stem": 6.0,
    "text_height": 2.5,
    "text_rise": 0.8,
    "text_inset": 0.75,
    "shelf_extra": 1.5,
    "plan_padding": 1.0,
}


# ── section marks, slopes, soils (v1.7) ─────────────────────────────────────

#: Линия сечения (ГОСТ 2.303 / 2.305 as the skill states them): open strokes
#: 8-20 mm at S-1.5S (0.7 here), arrows across the strokes showing the view
#: direction, the label beside each arrow. Lengths in mm of paper.
SECTION_MARK = {
    "stroke": 12.0,
    "arrow_inset": 2.0,
    "arrow_shaft": 6.0,
    "arrow_head": 3.0,
    "arrow_half_width": 0.6,
    "label_height": 5.0,
    "label_gap": 1.5,
    "layer": "Разрез",
}

#: Уклон (ГОСТ Р 21.101 as the skill states it): on a section the sign "∠"
#: (drawn as two strokes, the acute angle pointing down the slope) and the
#: value; on a plan an arrow down the slope with the value over it.
SLOPE = {"sign_length": 5.0, "sign_rise": 1.6, "text_height": 2.5, "arrow_length": 12.0}

#: ГОСТ 21.302-2013 табл. 4 as the skill's ``spds:fam`` draws them, mapped to
#: the nearest pattern AutoCAD and ezdxf both carry: (pattern, extra angle,
#: pattern's own spacing in mm, default spacing on paper in mm, note). The
#: pattern is an approximation of the conventional sign, said so in the result.
SOILS: dict[str, tuple[str, float, float, float, str]] = {
    "суглинок": ("ANSI31", 15.0, 3.175, 2.5, "solid lines at 60°"),
    "супесь": ("DASH", 60.0, 3.175, 2.5, "dashed lines at 60°"),
    "глина": ("LINE", 0.0, 3.175, 2.0, "solid horizontal lines"),
    "насыпной": ("ANSI37", 0.0, 3.175, 2.5, "a 45° / 135° grid"),
    "песок": ("DOTS", 0.0, 1.5875, 1.5, "dots; the grade letter in a circle"),
}

#: Sand grades: letter in the circle and the skill's dot spacing (mm of paper).
SAND_GRADES: dict[str, tuple[str, float]] = {
    "гравелистый": ("Г", 2.0),
    "крупный": ("К", 1.8),
    "средний": ("С", 1.5),
    "мелкий": ("М", 1.2),
    "пылеватый": ("П", 1.0),
}

LAYERS["Разрез"] = (7, "Continuous", 0.70)
LAYERS["УГВ"] = (5, "GOST_DASH", 0.25)
