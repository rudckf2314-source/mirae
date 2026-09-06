import re
from typing import Any

from .law_api_client import LawAPIClient


class LawReferenceResolver:
    """
    법령 내부 참조를 실제 조문/항/호 내용으로 변환한다.

    예:
    - 제2조제1항제1호
    - 제2조제1항제1호의2
    - 제14조제1항제4호

    시행령에서 '법 제25조'처럼 표현되면
    상위 법률을 조회한다.
    """

    REFERENCE_PATTERN = re.compile(
        r"(?:법\s*)?"
        r"제(?P<article>\d+)조"
        r"(?:제(?P<paragraph>\d+)항)?"
        r"(?:제(?P<item>\d+)호(?:의(?P<item_sub>\d+))?)?"
    )

    def __init__(self):
        self.client = LawAPIClient()

    def extract_references(
        self,
        text: str,
    ) -> list[dict[str, Any]]:
        """
        문장에서 법령 참조를 추출한다.
        """

        if not text:
            return []

        references = []

        for match in self.REFERENCE_PATTERN.finditer(text):

            raw = match.group(0)

            references.append({
                "raw": raw,
                "is_parent_law": raw.strip().startswith("법 "),
                "article": match.group("article"),
                "paragraph": match.group("paragraph"),
                "item": match.group("item"),
                "item_sub": match.group("item_sub"),
            })

        return references

    def resolve_reference(
        self,
        current_law_name: str,
        reference: dict[str, Any],
    ) -> dict[str, Any] | None:

        target_law_name = self._resolve_target_law(
            current_law_name=current_law_name,
            is_parent_law=reference["is_parent_law"],
        )

        article = self.client.get_article(
            target_law_name,
            reference["article"],
        )

        if article is None:
            return None

        paragraph_no = reference["paragraph"]
        item_no = reference["item"]
        item_sub = reference["item_sub"]

        result = {
            "reference": reference["raw"],
            "law_name": article["law_name"],
            "article_no": article["article_no"],
            "article_title": article["article_title"],
            "article_effective_date": article["article_effective_date"],
            "paragraph_no": paragraph_no,
            "item_no": None,
            "text": None,
        }

        # 조문 전체 참조
        if paragraph_no is None:
            result["text"] = self._article_to_text(article)
            return result

        paragraph = self._find_paragraph(
            article,
            paragraph_no,
        )

        if paragraph is None:
            return None

        # 항까지만 참조
        if item_no is None:
            result["text"] = paragraph.get("text")
            return result

        target_item = item_no

        if item_sub:
            target_item += f"의{item_sub}"

        result["item_no"] = target_item

        item = self._find_item(
            paragraph,
            target_item,
        )

        if item is None:
            return None

        result["text"] = item.get("text")

        return result

    def resolve_article_references(
        self,
        current_law_name: str,
        article: dict[str, Any],
    ) -> list[dict[str, Any]]:

        results = []
        seen = set()

        for paragraph in article.get("paragraphs", []):

            texts = [paragraph.get("text", "")]

            for item in paragraph.get("items", []):
                texts.append(item.get("text", ""))

            for text in texts:

                references = self.extract_references(text)

                for reference in references:

                    key = reference["raw"]

                    if key in seen:
                        continue

                    seen.add(key)

                    resolved = self.resolve_reference(
                        current_law_name,
                        reference,
                    )

                    if resolved:
                        resolved["origin_text"] = text
                        results.append(resolved)

        return results

    @staticmethod
    def _resolve_target_law(
        current_law_name: str,
        is_parent_law: bool,
    ) -> str:

        if not is_parent_law:
            return current_law_name

        if current_law_name.endswith(" 시행령"):
            return current_law_name.removesuffix(" 시행령")

        if current_law_name.endswith(" 시행규칙"):
            return current_law_name.removesuffix(" 시행규칙")

        return current_law_name

    @staticmethod
    def _find_paragraph(
        article: dict[str, Any],
        paragraph_number: str,
    ) -> dict[str, Any] | None:

        target_index = int(paragraph_number) - 1

        paragraphs = article.get("paragraphs", [])

        if (
            target_index < 0
            or target_index >= len(paragraphs)
        ):
            return None

        return paragraphs[target_index]

    @staticmethod
    def _find_item(
        paragraph: dict[str, Any],
        target_item: str,
    ) -> dict[str, Any] | None:

        normalized_target = (
            target_item
            .replace(".", "")
            .strip()
        )

        for item in paragraph.get("items", []):

            item_no = (
                str(item.get("item_no", ""))
                .replace(".", "")
                .strip()
            )

            if item_no == normalized_target:
                return item

        return None

    @staticmethod
    def _article_to_text(
        article: dict[str, Any],
    ) -> str:

        lines = []

        for paragraph in article.get("paragraphs", []):

            if paragraph.get("text"):
                lines.append(paragraph["text"])

            for item in paragraph.get("items", []):

                if item.get("text"):
                    lines.append(item["text"])

        return "\n".join(lines)