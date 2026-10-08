"""Expose pytest failures as GitHub check annotations without requiring log access."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def escape_annotation(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    report = Path(sys.argv[1])
    if not report.exists():
        return 0

    root = ET.parse(report).getroot()
    failures = []
    for case in root.iter("testcase"):
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        if failure is None:
            continue
        test_name = "::".join(filter(None, (case.get("classname"), case.get("name"))))
        message = failure.get("message") or (failure.text or "").strip()
        failures.append((test_name, message[:5000]))

    for test_name, message in failures:
        print(f"::error title=pytest failure::{escape_annotation(test_name + ': ' + message)}")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
