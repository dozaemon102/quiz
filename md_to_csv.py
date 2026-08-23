#!/usr/bin/env python3
"""Convert Obsidian-style multiple-choice quiz Markdown files to CSV."""

#python md_to_csv.py "02. Generative AI Leader.md" "Generative AI Leader.csv"

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CSV_COLUMNS = [
    "No.",
    "問題文",
    "選択肢A",
    "選択肢B",
    "選択肢C",
    "選択肢D",
    "正解",
    "解説",
    "分野",
]

QUESTION_RE = re.compile(r"^#####\s+Q(?P<number>\d+)\.\s*(?P<question>.+?)\s*$")
CHOICE_RE = re.compile(r"^-\s+\*\*(?P<label>[A-D])\.\*\*\s*(?P<text>.+?)\s*$")
ANSWER_RE = re.compile(r"^>\s*\*\*正解:\s*(?P<answer>[A-D])\*\*\s*$")
SECTION_RE = re.compile(r"^>\s*\[!NOTE\]-\s*(?P<section>.+?)\s*$")
RELATED_RE = re.compile(r"^>\s*\*\*関連:\*\*")
QUESTION_PREFIX_RE = re.compile(r"^#####\s+Q\d+\.")


@dataclass
class QuizQuestion:
    number: int
    question: str
    choices: dict[str, str]
    answer: str
    explanation: str
    domain: str


def strip_quote(line: str) -> str:
    """Remove one Obsidian Markdown quote marker and surrounding whitespace."""
    return re.sub(r"^>\s?", "", line).strip()


def parse_markdown(text: str, default_domain: str) -> list[QuizQuestion]:
    """Parse questions in the quiz Markdown convention used by this repository."""
    lines = text.splitlines()
    questions: list[QuizQuestion] = []
    current_domain = default_domain
    index = 0

    while index < len(lines):
        section_match = SECTION_RE.match(lines[index])
        if section_match:
            current_domain = section_match.group("section")
            index += 1
            continue

        question_match = QUESTION_RE.match(lines[index])
        if not question_match:
            index += 1
            continue

        number = int(question_match.group("number"))
        question = question_match.group("question")
        choices: dict[str, str] = {}
        answer = ""
        explanation_lines: list[str] = []
        index += 1
        in_explanation = False

        while index < len(lines):
            line = lines[index]

            if QUESTION_PREFIX_RE.match(line) or SECTION_RE.match(line):
                break

            choice_match = CHOICE_RE.match(line)

            if choice_match:
                choices[choice_match.group("label")] = choice_match.group("text")
                index += 1
                continue

            answer_match = ANSWER_RE.match(line)
            if answer_match:
                answer = answer_match.group("answer")
                in_explanation = True
                index += 1
                continue

            if in_explanation:
                if RELATED_RE.match(line):
                    in_explanation = False
                elif line.startswith(">"):
                    value = strip_quote(line)
                    if value and value != "---":
                        explanation_lines.append(value)
            index += 1

        missing_choices = [label for label in "ABCD" if label not in choices]
        if missing_choices:
            raise ValueError(f"Q{number}: 選択肢が不足しています: {', '.join(missing_choices)}")
        if not answer:
            raise ValueError(f"Q{number}: 正解が見つかりません")
        if not explanation_lines:
            raise ValueError(f"Q{number}: 解説が見つかりません")

        questions.append(
            QuizQuestion(
                number=number,
                question=question,
                choices=choices,
                answer=answer,
                explanation=" ".join(explanation_lines),
                domain=current_domain,
            )
        )

    if not questions:
        raise ValueError("質問（`##### Q番号.`）が見つかりません")

    numbers = [question.number for question in questions]
    if len(numbers) != len(set(numbers)):
        raise ValueError("問題番号が重複しています")

    return questions


def write_csv(questions: list[QuizQuestion], output_path: Path, start_number: int | None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for offset, item in enumerate(questions):
            writer.writerow(
                {
                    "No.": start_number + offset if start_number is not None else item.number,
                    "問題文": item.question,
                    "選択肢A": item.choices["A"],
                    "選択肢B": item.choices["B"],
                    "選択肢C": item.choices["C"],
                    "選択肢D": item.choices["D"],
                    "正解": item.answer,
                    "解説": item.explanation,
                    "分野": item.domain,
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="変換元の Markdown ファイル")
    parser.add_argument("output", type=Path, help="出力先 CSV ファイル")
    parser.add_argument(
        "--domain",
        default="",
        help="Markdown に NOTE セクションがない場合に使用する分野名",
    )
    parser.add_argument(
        "--start-number",
        type=int,
        help="CSV の No. をこの番号から連番で採番する（省略時は Q 番号を使用）",
    )
    args = parser.parse_args()

    try:
        markdown = args.input.read_text(encoding="utf-8")
        questions = parse_markdown(markdown, args.domain)
        write_csv(questions, args.output, args.start_number)
    except (OSError, ValueError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1

    print(f"{len(questions)} 問を {args.output} に出力しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


