"""營收品質實驗獨立報告；新排序不能借用RS基準的績效或校準率。"""
import hashlib
import json

from . import research_results

RESULT_PATH = research_results.ROOT / "research" / "growth_quality_v1.json"


def fingerprint():
    digest = hashlib.sha256(research_results.fingerprint().encode())
    for path in [research_results.ROOT / "app" / name for name in
                 ("growth_quality.py", "growth_research.py", "decision_combinations.py")
                 ] + [research_results.ROOT / "backtest_growth_quality.py"]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_report():
    if not RESULT_PATH.exists():
        return {"status": "unvalidated", "message": "營收品質尚無歷史研究", "variants": []}
    try:
        report = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "unavailable", "message": "營收品質研究檔無法讀取", "variants": []}
    if report.get("fingerprint") != fingerprint():
        return {"status": "outdated", "message": "營收品質規則已變更，需重跑研究", "variants": []}
    return report
