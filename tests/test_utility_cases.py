from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.batch_import import parse_tabular
from app.pipeline import _extract_expression, _guess_chart_kind, _to_number_list


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("帮我计算 3+4", "3+4"),
        ("计算 (5*2)-3", "(5*2)-3"),
        ("今天天气怎么样", ""),
        ("10 除以 2 等于几", ""),
    ],
)
def test_extract_expression(question: str, expected: str) -> None:
    assert _extract_expression(question) == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("画一个饼图", "pie"),
        ("画折线图看趋势", "line"),
        ("散点图分析分布", "scatter"),
        ("画柱状图", "bar"),
    ],
)
def test_guess_chart_kind(question: str, expected: str) -> None:
    assert _guess_chart_kind(question) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([1, 2, 3], [1.0, 2.0, 3.0]),
        (["1", "bad", "3"], [1.0, 3.0]),
        ("not-a-list", [1.0]),
    ],
)
def test_to_number_list(value: object, expected: list[float]) -> None:
    assert _to_number_list(value, [1.0]) == expected


def test_parse_tabular_csv(tmp_path: Path) -> None:
    source = tmp_path / "sample.csv"
    with source.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["filename", "content"])
        writer.writeheader()
        writer.writerow({"filename": "a.txt", "content": "内容甲"})
        writer.writerow({"filename": "b.txt", "content": "内容乙"})
    rows = parse_tabular(source)
    assert len(rows) == 2
    assert rows[0]["filename"] == "a.txt"


def test_parse_tabular_rejects_unknown_suffix(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        parse_tabular(tmp_path / "sample.xyz")
