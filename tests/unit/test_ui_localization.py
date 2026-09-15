from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


STATIC_ROOT = Path(__file__).resolve().parents[2] / "src" / "linkloom" / "ui" / "static"


def _run_i18n(expression: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the Product UI localization contract test.")
    module_path = json.dumps(str(STATIC_ROOT / "i18n.js"))
    command = (
        f"const i18n = require({module_path}); "
        f"const value = ({expression}); "
        "process.stdout.write(JSON.stringify(value));"
    )
    completed = subprocess.run(
        [node, "-e", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def test_locale_resolution_prefers_url_then_storage_then_browser() -> None:
    result = _run_i18n(
        "["
        "i18n.resolveLocale({urlLocale:'zh',storedLocale:'en-US',browserLanguages:['en-US']}),"
        "i18n.resolveLocale({urlLocale:null,storedLocale:'zh-CN',browserLanguages:['en-US']}),"
        "i18n.resolveLocale({urlLocale:null,storedLocale:null,browserLanguages:['zh-Hans-CN']}),"
        "i18n.resolveLocale({urlLocale:'fr',storedLocale:null,browserLanguages:['de-DE']})"
        "]"
    )

    assert result == ["zh-CN", "zh-CN", "zh-CN", "en-US"]


def test_chinese_messages_format_counts_locations_statuses_and_support_labels() -> None:
    result = _run_i18n(
        "({"
        "title:i18n.t('zh-CN','initial.titleLine1'),"
        "count:i18n.documentCountLabel('zh-CN',6),"
        "location:i18n.locationLabel('zh-CN',10,12),"
        "status:i18n.displayStatus('zh-CN','in_progress'),"
        "support:i18n.supportLabel('zh-CN','Rejected alternative: Borealis B')"
        "})"
    )

    assert result == {
        "title": "找到最终决定。",
        "count": "6 篇项目记录",
        "location": "第 10–12 行",
        "status": "进行中",
        "support": "已拒绝的替代方案：Borealis B",
    }


def test_english_messages_remain_the_default_and_unknown_values_fail_softly() -> None:
    result = _run_i18n(
        "({"
        "title:i18n.t('en-US','initial.titleLine1'),"
        "count:i18n.documentCountLabel('en-US',1),"
        "unknown:i18n.displayStatus('en-US','waiting_review')"
        "})"
    )

    assert result == {
        "title": "Find the decision.",
        "count": "1 project note",
        "unknown": "waiting review",
    }


def test_chinese_and_english_message_catalogs_have_the_same_contract() -> None:
    result = _run_i18n(
        "({"
        "english:Object.keys(i18n.MESSAGES['en-US']).sort(),"
        "chinese:Object.keys(i18n.MESSAGES['zh-CN']).sort()"
        "})"
    )

    assert result["chinese"] == result["english"]
