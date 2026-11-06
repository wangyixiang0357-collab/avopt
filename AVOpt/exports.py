"""结果导出为真正的 XLSX，保留输入、气象、试验点和诊断。"""
import io
import json

def excel(result):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    if not isinstance(result, dict) or not isinstance(result.get("opt"), dict):
        raise ValueError("缺少完整计算结果")
    wb = Workbook()
    wb.remove(wb.active)
    def safe(value):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    def mapping(name, obj):
        ws = wb.create_sheet(name)
        ws.append(["字段", "数值 / 说明"])
        for k, v in obj.items():
            ws.append([k, safe(v)])
    mapping("推荐方案", result["opt"])
    mapping("输入参数", {**result.get("request", {}), "crop_info": result.get("crop_info", {})})
    mapping("气象与订正", result.get("meta", {}))
    mapping("诊断与说明", {"version": result.get("version"), "created_at": result.get("created_at"), "warnings": result.get("warnings"), "model_notes": result.get("model_notes"), "search_summary":result.get("search_summary"), **result.get("diagnostics", {})})
    for name, rows in [("试验点", result.get("runs", [])), ("局部敏感性", result.get("sensitivity", []))]:
        ws = wb.create_sheet(name)
        if rows:
            fields = list(rows[0]); ws.append(fields)
            for r in rows:
                ws.append([safe(r.get(k)) for k in fields])
    for ws in wb:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F3B57")
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(65, max(15, max(len(str(c.value or "")) for c in col) + 2))
    stream = io.BytesIO(); wb.save(stream)
    return stream.getvalue()
