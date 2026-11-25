#!/bin/zsh
# 매일 실행: 수집 후 report.html 갱신
cd "$(dirname "$0")"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 collector.py >> logs/collector.log 2>&1
