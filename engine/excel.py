"""Conservative header detection: never guess identifiers from numeric columns."""
import datetime as dt
import hashlib
import json
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

ALIASES = {
    "invoice_number": ["发票号码", "发票号", "数电票号码", "数电发票号码", "电子发票号码", "票据号码", "invoice number", "invoice no", "invoice no."],
    "invoice_code": ["发票代码", "票据代码", "invoice code"],
    "issue_date": ["开票日期", "开票时间", "发票日期", "发票开具日期", "开具日期", "开具时间", "日期", "invoice date", "issue date"],
    "amount": ["价税合计", "价税合计小写", "价税合计金额", "含税金额", "含税总金额", "合计金额含税", "金额含税", "总金额", "合计金额", "发票金额", "金额", "total amount", "invoice amount", "amount"],
}


def normalize(value):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", unicodedata.normalize("NFKC", str(value or "")).lower()).replace("_", "")


def header_field(value):
    label = normalize(value)
    # Common unit annotations; do not remove meaningful qualifications such as 不含税.
    label = re.sub(r"(?:人民币元|人民币|rmb|cny|元)$", "", label)
    for field, aliases in ALIASES.items():
        for rank, alias in enumerate(aliases):
            if label == normalize(alias):
                return field, rank
    return None, None


def detect_header(values, previous=None):
    found, ranks, labels, ambiguous = {}, {}, {}, set()
    for index, value in enumerate(values):
        field, rank = header_field(value)
        label = str(value or "").strip()
        if not field and previous and index < len(previous):
            label = f"{previous[index] or ''}{value or ''}"
            field, rank = header_field(label)
        if not field:
            continue
        if field in found and rank == ranks[field]:
            ambiguous.add(field)
        if field not in found or rank < ranks[field]:
            found[field], ranks[field], labels[field] = index, rank, label
            ambiguous.discard(field)
    for field in ambiguous:
        found.pop(field, None)
    return found, labels


def parse_number(value):
    if isinstance(value, (int, float, Decimal)):
        raise ValueError("发票号码以数字存储，Excel 可能已丢失精度；请从原始数据重新导出为文本")
    number = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).strip().lstrip("'"))
    if not re.fullmatch(r"[0-9]{20}", number):
        raise ValueError("当前支持20位数电发票号码，请检查号码是否完整并以文本存储")
    return number


def parse_date(value, epoch):
    from openpyxl.utils.datetime import from_excel
    if isinstance(value, (dt.datetime, dt.date)):
        result = value.date() if isinstance(value, dt.datetime) else value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("开票日期无效")
        if 19000101 <= value <= 29991231 and value == int(value):
            result = dt.datetime.strptime(str(int(value)), "%Y%m%d").date()
        else:
            converted = from_excel(value, epoch=epoch)
            if not isinstance(converted, dt.datetime):
                raise ValueError("开票日期无效")
            result = converted.date()
    else:
        text = unicodedata.normalize("NFKC", str(value or "")).strip()
        if re.fullmatch(r"\d{8}", text):
            result = dt.datetime.strptime(text, "%Y%m%d").date()
        else:
            match = re.match(r"^(\d{4})[年./\-](\d{1,2})[月./\-](\d{1,2})(?:日|\s|T|$)", text)
            if not match:
                raise ValueError("开票日期无法识别")
            result = dt.date(*map(int, match.groups()))
    if result > dt.date.today() or result.year < 2000:
        raise ValueError("开票日期超出有效范围")
    return result.strftime("%Y%m%d")


def parse_amount(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"[\s,¥￥元]", "", text)
    text = re.sub(r"^(?:CNY|RMB)", "", text, flags=re.I)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        amount = Decimal(text)
        if not amount.is_finite() or not amount or abs(amount) > Decimal("999999999999.99"):
            raise ValueError("金额无效或为空")
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not amount:
            raise ValueError("金额不能为零")
        return f"{amount:.2f}"
    except InvalidOperation as exc:
        raise ValueError("金额无法识别，请检查单元格数值或公式缓存") from exc


def read_workbook(source):
    from openpyxl import load_workbook
    from openpyxl.utils.datetime import CALENDAR_WINDOWS_1900, CALENDAR_MAC_1904
    source = Path(source)
    if not source.is_file() or source.suffix.lower() not in (".xlsx", ".xlsm", ".xls"):
        raise ValueError("请选择 Excel 文件（.xlsx / .xlsm / .xls）")
    if source.stat().st_size > 40 * 1024 * 1024:
        raise ValueError("Excel 文件过大，请拆分到40 MB以内")
    if source.suffix.lower() == ".xls":
        import xlrd
        workbook = xlrd.open_workbook(str(source), on_demand=True)
        epoch = CALENDAR_MAC_1904 if workbook.datemode else CALENDAR_WINDOWS_1900
        sheets = ((s.name, ([cell.value for cell in s.row(i)] for i in range(s.nrows))) for s in workbook.sheets())
        close = workbook.release_resources
    else:
        workbook = load_workbook(source, read_only=True, data_only=True)
        epoch = workbook.epoch
        sheets = ((s.title, s.iter_rows(values_only=True)) for s in workbook.worksheets)
        close = workbook.close
    invoices, issues, mappings, warnings = [], [], [], []
    seen, conflicts = {}, set()
    duplicates = 0
    try:
        for name, rows in sheets:
            headers = None
            previous = None
            for row_index, values in enumerate(rows, 1):
                if row_index > 50000:
                    raise ValueError(f"{name} 超过50000行，请拆分后导入")
                if headers is None:
                    if row_index > 100:
                        break
                    found, labels = detect_header(values, previous)
                    previous = values
                    if not {"invoice_number", "issue_date", "amount"} <= found.keys():
                        continue
                    headers = found
                    mappings.append({"sheet": name, "row": row_index, "labels": labels})
                    if normalize(labels["amount"]) in ("金额", "发票金额", "合计金额", "总金额", "amount", "invoiceamount", "totalamount"):
                        warnings.append(f"{name}：将“{labels['amount']}”作为价税合计，请确保包含税额。")
                    continue
                def cell(field):
                    index = headers.get(field)
                    return values[index] if index is not None and index < len(values) else None
                number = cell("invoice_number")
                if number is None or not str(number).strip():
                    continue
                label = normalize(number)
                if header_field(number)[0] == "invoice_number" or label.startswith(("合计", "总计", "小计", "汇总", "total")):
                    continue
                location = f"{name} 第{row_index}行"
                parsed_number = None
                try:
                    parsed_number = parse_number(number)
                    invoice = {
                        "invoice_number": parsed_number,
                        "invoice_code": "",  # 20位数电发票无需代码。
                        "issue_date": parse_date(cell("issue_date"), epoch),
                        "amount": parse_amount(cell("amount")),
                        "sheet": name, "excel_row": row_index,
                    }
                    identity = [invoice[k] for k in ("invoice_code", "invoice_number", "issue_date", "amount")]
                    invoice["id"] = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
                    old = seen.get(invoice["invoice_number"])
                    if old:
                        if old["id"] == invoice["id"] and invoice["invoice_number"] not in conflicts:
                            duplicates += 1
                        else:
                            if invoice["invoice_number"] not in conflicts:
                                invoices.remove(old)
                                issues.append({"location": f"{old['sheet']} 第{old['excel_row']}行", "invoice_number": old["invoice_number"], "message": "同一发票号码对应不同日期或金额，请核对"})
                            conflicts.add(invoice["invoice_number"])
                            issues.append({"location": location, "invoice_number": parsed_number, "message": "同一发票号码对应不同日期或金额，请核对"})
                        continue
                    seen[invoice["invoice_number"]] = invoice
                    invoices.append(invoice)
                except (ValueError, OverflowError, TypeError) as exc:
                    issues.append({"location": location, "invoice_number": parsed_number, "message": str(exc)})
    finally:
        close()
    if not mappings:
        raise ValueError("未识别到发票表头。需要发票号码、开票日期和价税合计（或金额）三列；表头应在前100行。")
    if not invoices and not issues:
        raise ValueError("找到表头，但没有可查验的发票数据")
    fingerprint = hashlib.sha256(json.dumps(sorted(i["id"] for i in invoices)).encode()).hexdigest()[:24]
    return {"invoices": invoices, "issues": issues, "warnings": list(dict.fromkeys(warnings)), "mappings": mappings,
            "duplicates": duplicates, "fingerprint": fingerprint, "filename": source.name, "total": len(invoices)}
