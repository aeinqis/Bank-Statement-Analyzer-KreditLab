import types

import report_utils


def test_prepare_report_for_export_adds_defaults_without_crashing():
    data = {
        "report_info": {"company_name": "Demo Co", "schema_version": "6.3.5"},
        "accounts": [],
        "monthly_analysis": [],
        "consolidated": {},
    }

    result = report_utils.prepare_report_for_export(data)

    assert result["report_metadata"]["format"] == "kredit_lab_interactive_report"
    assert result["observations"]["positive"] == []
    assert result["observations"]["concerns"] == []
    assert result["ai_editing_instructions"]["editable_fields"]
