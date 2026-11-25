"""Читання PDF, розділи виписки та координати доказів."""

from dataclasses import dataclass
from datetime import datetime
import re
import unicodedata

import fitz


MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 12
DATE = re.compile(r"(?<!\d)\d{1,2}\.\d{1,2}\.\d{4}(?!\d)")
CODE = re.compile(r"(?<!\d)[0-9]{8}(?!\d)")
NUMBER = re.compile(r"(?<!\d)\d[\d -]{7,32}\d(?!\d)")
LABELS = {
    "name": r"^(?:повне )?найменування юридичної особи\s*:",
    "code": r"^(?:ідентифікаційний код юридичної особи|код (?:за )?єдрпоу(?: юридичної особи)?)\s*:",
    "address": r"^місцезнаходження юридичної особи\s*:",
    "registration": r"^дата (?:та номер|та час) запису(?:\s|:|$)",
    "registration_number": r"^номер запису(?:\s|:|$)",
    "management": r"^(?:прізвище,\s*ім['’ʼ]?я та по батькові осіб, які мають право|органи управління|керівник юридичної особи)\b",
    "issued_date": r"^(?:номер,? дата та час формування виписки|дата формування виписки)\s*:",
    "owners": r"^(?:відомості про засновників|перелік засновників|засновники|учасники)(?:\s|:|$)",
    "ubo": r"^(?:відомості про кінцев(?:ого|их) бенефіціарн(?:ого|их) власник(?:а|ів)|кінцевий бенефіціарний власник)(?:\s|:|$)",
    "company_status": r"^(?:стан юридичної особи|статус юридичної особи)\s*:",
    "termination": r"^(?:(?:відомості|дані) про )?(?:перебування юридичної особи в (?:процесі|стані) припинення|припинення юридичної особи)\b",
    "activities": r"^(?:(?:основний )?вид(?:и)? економічної діяльності|основний вид діяльності)(?:\s|:|$)",
    "boundary": r"^(?:відомості, отримані|дані про перебування|дані про основний|дані про взяття|органи державної|місце зберігання|назва установи|реєстратор|державний реєстратор|навчальний зразок|службова примітка)\b",
}
NON_COMPANY_SECTION = re.compile(
    r"^(?:(?:відомості|дані|перелік) про )?(?:засновник|учасник|кінцев|бенефіціар|"
    r"підписант|представник|керівник|органи управління|державн\w+ (?:реєстратор|орган)|"
    r"реєстратор|податков\w+ |контролююч\w+ орган|держорган)"
)
CODE_LABEL = re.compile(r"^(?:єдрпоу|ідентифікаційний код|реєстраційний код юридичної особи)\s*:")
ALTERNATE_NAME_LABEL = re.compile(r"^(?:повне |скорочене )?найменування\s*:")
TEXT_HEADING = re.compile(r"^[^«»\"\d:]+:")
FIELD_LABELS = {
    "name": "Повне найменування",
    "code": "Код ЄДРПОУ компанії",
    "address": "Місцезнаходження",
    "registration_date": "Дата реєстраційного запису",
    "registration_number": "Номер реєстраційного запису",
    "management": "Керівник і підписанти",
    "issued_date": "Дата формування виписки",
    "owners": "Засновники й учасники",
    "ubo": "Кінцевий бенефіціарний власник (КБВ)",
    "company_status": "Стан юридичної особи",
    "termination": "Дані про припинення",
    "activities": "Види діяльності",
}
ABSENT = re.compile(r"^(?:(?:відомості|дані) (?:відсутні|не зазначені)|інформація відсутня|відсутні|відсутній|немає|не зазначено|-)[. ]*$", re.I)
SPACE = re.compile(r"\s+")
CHARACTERS = str.maketrans({"’": "'", "ʼ": "'", "‘": "'", "«": '"', "»": '"', "“": '"', "”": '"', "–": "-", "—": "-", "\u00ad": None, "\u200b": None})


class DocumentError(ValueError):
    """Помилка прийняття або читання документа."""


def normalize(value):
    return SPACE.sub(" ", unicodedata.normalize("NFKC", value).translate(CHARACTERS)).strip().casefold()


def valid_date(value):
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return None


def company_code_proof(pages, page_number, fragment, code):
    """Знайти цитату біля власного коду до початку розділів інших осіб."""
    quote = normalize(fragment)
    for page in pages:
        if page.kind != "text":
            continue
        identity = []
        for line in page.text.splitlines():
            normal = normalize(line)
            if NON_COMPANY_SECTION.match(normal) or re.match(LABELS["management"], normal) or re.match(LABELS["boundary"], normal):
                break
            if normal:
                identity.append(normal)
        else:
            if page.number < page_number:
                continue
        if page.number != page_number:
            return False
        for index, line in enumerate(identity):
            if not (re.match(LABELS["code"], line) or CODE_LABEL.match(line)):
                continue
            inline = line.partition(":")[2].strip()
            following = identity[index + 1] if index + 1 < len(identity) else ""
            if inline == code or (not inline and following == code):
                return quote in " ".join(identity)
        return False
    return False


@dataclass
class Page:
    number: int
    text: str
    kind: str


@dataclass
class Section:
    page: int
    lines: list
    has_body: bool = True

    @property
    def fragment(self):
        return "\n".join(self.lines)


def open_pdf(data):
    if not data.startswith(b"%PDF-") or len(data) > MAX_BYTES:
        raise DocumentError("Завантажте PDF розміром до 8 МБ.")
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as error:
        raise DocumentError("PDF пошкоджений або його формат не підтримується.") from error
    if document.needs_pass:
        document.close()
        raise DocumentError("PDF захищений паролем. Потрібна копія без пароля.")
    if not 1 <= len(document) <= MAX_PAGES:
        document.close()
        raise DocumentError("PDF має містити від 1 до 12 сторінок.")
    return document


def page_content(document):
    pages = []
    for page in document:
        text = page.get_text(sort=True).strip()
        words = re.findall(r"[^\W\d_]{3,}", text)
        substantial_image = False
        area = page.rect.width * page.rect.height
        if area and len(words) < 12:
            boxes = [fitz.Rect(info["bbox"]) for info in page.get_image_info()]
            substantial_image = any(box.width * box.height > area * 0.55 for box in boxes)
        kind = "scan" if not words or (substantial_image and len(words) < 12) else "text"
        pages.append(Page(page.number + 1, text if kind == "text" else "", kind))
    return pages


def sections_from(pages):
    sections = {}
    form = "unknown"
    expanded = False
    company_identity = True
    for page in pages:
        if page.kind != "text":
            continue
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        if not lines:
            continue
        top = lines[:8]
        if any(re.fullmatch(r"виписка(?:\s+з єдиного.*)?", normalize(line)) for line in top):
            form = "statement"
        if any(re.fullmatch(r"витяг(?:\s+з єдиного.*)?", normalize(line)) for line in top):
            form = "extract"
        blocks = []
        active_key = None
        active = []
        before_sections = []
        for line in lines:
            normal = normalize(line)
            key = next((field for field, pattern in LABELS.items() if re.match(pattern, normal)), None)
            if key is None and NON_COMPANY_SECTION.match(normal) and normal.endswith(":"):
                key = "boundary"
            if key:
                if active_key is not None:
                    blocks.append((active_key, active))
                active_key = key
                active = [line]
            elif active_key is not None:
                active.append(line)
            elif page.number == 1:
                before_sections.append(line)
        if active_key is not None:
            blocks.append((active_key, active))
        name_start = re.compile(r"^(?:товариство|приватне підприємство|акціонерне товариство|комунальне підприємство|громадська|благодійна|тов\s)", re.I)
        for index, line in enumerate(before_sections):
            if ALTERNATE_NAME_LABEL.match(normalize(line)):
                break
            if name_start.match(line):
                company = [line]
                for continuation in before_sections[index + 1:]:
                    if TEXT_HEADING.match(continuation):
                        break
                    company.append(continuation)
                sections.setdefault("name", []).append(Section(page.number, company))
                break
        in_company_identity = company_identity and (page.number == 1 or not sections.get("code"))
        for key, block in blocks:
            if key in {"owners", "ubo", "management"}:
                expanded = expanded or key == "owners"
                in_company_identity = False
                company_identity = False
            if key == "code" and not in_company_identity:
                continue
            if key == "boundary":
                in_company_identity = False
                company_identity = False
                continue
            for name, value_lines, has_body in field_values(key, block):
                sections.setdefault(name, []).append(Section(page.number, value_lines, has_body))
    return form, expanded, sections


def field_values(key, block):
    heading = block[0]
    inline = heading.partition(":")[2].strip() if ":" in heading else ""
    body = ([inline] if inline else []) + block[1:]
    if key == "registration":
        body_text = " ".join(body)
        date = DATE.search(body_text)
        start = date.end() if date else 0
        remaining = re.sub(r"^\s*\d\d:\d\d(?::\d\d)?", "", body_text[start:])
        number = NUMBER.search(remaining)
        date_lines = [date.group()] if date else []
        number_lines = [number.group().strip()] if number else []
        return [
            ("registration_date", date_lines, bool(body)),
            ("registration_number", number_lines, bool(body)),
        ]
    if key == "issued_date":
        date = DATE.search(" ".join(body))
        body = [date.group()] if date else []
    if key == "management":
        body = [line for line in body if not normalize(line).startswith(("особи без довіреності", "особи, у тому числі", "у тому числі підписувати", "обмежень щодо представництва", "представництва від імені", "особи:"))]
        while body and not re.search(r"[\w)]$", body[0]) and body[0].endswith(":"):
            if len(body[0]) < 25:
                break
            body = body[1:]
    if key == "code":
        body = [body[0]] if body else []
    return [(key, body[:12], bool(body))]


def page_jpeg(page, target_width=960):
    if not 0 < page.rect.width <= 16000 or not 0 < page.rect.height <= 16000:
        raise DocumentError("Одна зі сторінок PDF має непідтримуваний розмір.")
    scale = min(target_width / page.rect.width, 1500 / page.rect.height, 2)
    return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("jpeg", jpg_quality=78)


def positions(page, fragment):
    """Повертає лише однозначні координати, у відсотках від зображення."""
    view = page.rect
    if not fragment or view.is_empty:
        return []
    queries = [piece.strip() for piece in fragment.splitlines() if piece.strip()]
    rectangles = []
    fallback_lines = None
    for query in queries:
        hits = page.search_for(query)
        if not hits:
            if fallback_lines is None:
                fallback_lines = [
                    (normalize(" ".join(span["text"] for span in line["spans"])), fitz.Rect(line["bbox"]))
                    for block in page.get_text("dict", sort=True)["blocks"] if block.get("type") == 0
                    for line in block.get("lines", [])
                ]
            hits = [box for text, box in fallback_lines if normalize(query) in text]
        if len(hits) != 1:
            continue
        rect = hits[0] * page.rotation_matrix
        rectangles.append({
            "x": round((rect.x0 - view.x0) / view.width * 100, 3),
            "y": round((rect.y0 - view.y0) / view.height * 100, 3),
            "w": round(rect.width / view.width * 100, 3),
            "h": round(rect.height / view.height * 100, 3),
        })
    return rectangles
