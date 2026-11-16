import io
import logging
import uuid
from pathlib import Path
from typing import ClassVar

import pandas as pd
from fastapi import status
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI

from app.assistant.schemas import ChatRequest
from app.assistant.service import AssistantService
from app.errors import AppError
from app.evaluation.prompts import EVAL_SYSTEM_PROMPT


class EvaluationService:
    """Batch evaluation of the assistant against a testbook CSV.

    Fires every question through the in-process `AssistantService` (one fresh session per row,
    so conversations do not bleed across questions), enriches each row with the extracted
    references and an LLM-as-judge verdict comparing the answer to the ground truth, and returns
    the same CSV with the evaluation columns appended. Mirrors `scripts/eval.py`, but wired
    through DI instead of HTTP calls to the live endpoint.
    """

    # Columns the input testbook CSV must carry; the enriched columns are appended on top.
    REQUIRED_COLUMNS: ClassVar[list[str]] = [
        "question",
        "ground_truth",
        "source_document",
        "page",
    ]
    # The endpoint is synchronous and sequential, and each row triggers several LLM calls. The
    # upstream proxy in qual/prod cuts any request that exceeds 120s (infrastructure
    # `upstream_timeout`), so a larger batch would blow past it and fail the whole upload. Cap the
    # rows per upload well under that ceiling.
    MAX_ROWS: ClassVar[int] = 5
    # Marker identity logged for the assistant calls fired by the batch evaluation: these are not
    # driven by a conversing end user, so the `[ASSISTANT]` log block shows the batch origin.
    EVALUATION_USER: ClassVar[str] = "evaluation@batch"

    def __init__(self, assistant: AssistantService, judge_llm: AzureChatOpenAI):
        self.assistant = assistant
        self.judge_llm = judge_llm
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def source_page_retrieved(row: pd.Series) -> bool:
        """Return True if the expected source document AND page (±2) were retrieved.

        Matches an extracted reference (title|page pair) whose title equals the stem
        of the expected source document and whose page is within ±2 of the testbook
        "page" column.
        """
        references: str = row.get("extracted_references") or ""
        source: str = row.get("source_document") or ""
        if not source or not references:
            return False
        try:
            expected_page = int(row.get("page"))
        except (TypeError, ValueError):
            return False
        source_stem = Path(source).stem
        for ref in references.split("; "):
            title, _, page_str = ref.rpartition("|")
            if Path(title).stem != source_stem:
                continue
            try:
                page = int(page_str)
            except ValueError:
                continue
            if abs(page - expected_page) <= 2:
                return True
        return False

    async def _judge_answer(self, ground_truth: str, answer: str) -> bool | None:
        if not ground_truth or not answer:
            return None
        messages = [
            SystemMessage(content=EVAL_SYSTEM_PROMPT),
            HumanMessage(content=f"GROUND TRUTH:\n{ground_truth}\n\nANSWER:\n{answer}"),
        ]
        response = await self.judge_llm.ainvoke(messages)
        return str(response.content).strip().lower().startswith("true")

    async def evaluate(self, csv_bytes: bytes) -> bytes:
        try:
            df = pd.read_csv(io.BytesIO(csv_bytes), delimiter=",")
        except (ValueError, pd.errors.ParserError) as exc:
            raise AppError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not parse the uploaded file as CSV: {exc}",
            ) from exc

        missing = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        if missing:
            raise AppError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Missing required columns: {', '.join(missing)}",
            )

        if len(df) > self.MAX_ROWS:
            raise AppError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Attention! Max rows = {self.MAX_ROWS} per upload (received {len(df)})."
                ),
            )

        total = len(df)
        self._logger.info("Loaded %d questions for evaluation", total)

        answers: list[str] = []
        rephrased_questions: list[str | None] = []
        extracted_documents: list[str] = []
        extracted_references: list[str] = []
        llm_eval: list[bool | None] = []

        for idx, row in df.iterrows():
            question = row["question"]
            self._logger.info("[%d/%d] %s", idx + 1, total, str(question)[:80])
            try:
                response = await self.assistant.chat(
                    ChatRequest(message=question, session_id=str(uuid.uuid4())),
                    self.EVALUATION_USER,
                )
                answers.append(response.answer)
                rephrased_questions.append(response.rephrased_question)
                titles = [
                    ref.document_title
                    for ref in response.references
                    if ref.document_title
                ]
                seen: set[str] = set()
                unique = [t for t in titles if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]
                extracted_documents.append("; ".join(unique))
                pairs = [
                    f"{ref.document_title}|{page}"
                    for ref in response.references
                    if ref.document_title
                    for page in (ref.page_number or [])
                ]
                extracted_references.append("; ".join(pairs))
                ground_truth = str(row.get("ground_truth") or "")
                verdict = await self._judge_answer(ground_truth, response.answer)
                llm_eval.append(verdict)
            except Exception as exc:  # noqa: BLE001
                self._logger.error("[%d/%d] failed — %s", idx + 1, total, exc)
                answers.append("")
                rephrased_questions.append(None)
                extracted_documents.append("")
                extracted_references.append("")
                llm_eval.append(None)

        df["rephrased_question"] = rephrased_questions
        df["answer"] = answers
        df["extracted_documents"] = extracted_documents
        df["extracted_references"] = extracted_references
        df["source_page_retrieved"] = df.apply(self.source_page_retrieved, axis=1)
        df["llm_eval"] = llm_eval

        buffer = io.StringIO()
        df.to_csv(buffer, index=False)
        self._logger.info("Evaluated %d rows", total)
        return buffer.getvalue().encode()
