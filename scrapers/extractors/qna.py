"""
Q&A Extractor - Extracts customer questions and expert answers from part pages.

Pagination Strategy:
Only extracts first page (~10 Q&A), which is already sorted by "Most Helpful".
This captures the highest-value Q&A without pagination overhead - helpful votes
indicate the most useful content for embeddings.
"""

import re
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException


def extract_qna(driver):
    """
    Extract Q&A from the current product page.

    Args:
        driver: Selenium WebDriver instance (already on the part page)

    Returns:
        list[dict]: List of Q&A dictionaries with fields:
            - question_id: str
            - asker: str
            - date: str
            - question: str
            - answer: str
            - model_number: str (if customer specified their model)
            - helpful_count: int
    """
    qna_list = []

    try:
        qna_containers = driver.find_elements(
            By.CSS_SELECTOR, "div.qna__question.js-qnaResponse"
        )

        for container in qna_containers:
            try:
                qna = {}

                # Question ID from element id attribute
                qna["question_id"] = container.get_attribute("id") or ""

                # Asker name
                try:
                    asker_el = container.find_element(By.CSS_SELECTOR, "div.title-md.bold")
                    qna["asker"] = asker_el.text.strip()
                except NoSuchElementException:
                    qna["asker"] = ""

                # Date
                try:
                    date_el = container.find_element(By.CSS_SELECTOR, "div.qna__question__date")
                    qna["date"] = date_el.text.strip()
                except NoSuchElementException:
                    qna["date"] = ""

                # Question text
                try:
                    question_els = container.find_elements(By.CSS_SELECTOR, ":scope > div.js-searchKeys")
                    if question_els:
                        qna["question"] = question_els[0].text.strip()
                    else:
                        all_search = container.find_elements(By.CSS_SELECTOR, "div.js-searchKeys")
                        if all_search:
                            qna["question"] = all_search[0].text.strip()
                        else:
                            qna["question"] = ""
                except NoSuchElementException:
                    qna["question"] = ""

                # Model number (optional)
                try:
                    model_el = container.find_element(
                        By.XPATH, ".//div[contains(@class, 'bold') and contains(text(), 'model number')]"
                    )
                    model_text = model_el.text.strip()
                    match = re.search(r'model number\s+(.+)', model_text, re.IGNORECASE)
                    qna["model_number"] = match.group(1).strip() if match else ""
                except NoSuchElementException:
                    qna["model_number"] = ""

                # Answer from PartSelect
                try:
                    answer_el = container.find_element(
                        By.CSS_SELECTOR, "div.qna__ps-answer__msg div.js-searchKeys"
                    )
                    qna["answer"] = answer_el.text.strip()
                except NoSuchElementException:
                    qna["answer"] = ""

                # Helpful count
                try:
                    helpful_el = container.find_element(By.CSS_SELECTOR, "p.js-displayRating")
                    helpful_count = helpful_el.get_attribute("data-found-helpful")
                    qna["helpful_count"] = int(helpful_count) if helpful_count else 0
                except (NoSuchElementException, ValueError):
                    qna["helpful_count"] = 0

                # Only add if we have content
                if qna.get("question") or qna.get("answer"):
                    qna_list.append(qna)

            except Exception as e:
                print(f"Error extracting Q&A: {e}")
                continue

    except Exception as e:
        print(f"Error finding Q&A containers: {e}")

    return qna_list


def format_for_embedding(qna, ps_number, part_name=None):
    """
    Format a Q&A into text suitable for vector embedding.

    Args:
        qna: Q&A dict from extract_qna
        ps_number: PartSelect part number (e.g., "PS11739091")
        part_name: Optional part name

    Returns:
        str: Formatted text for embedding
    """
    parts = [f"Part ID: {ps_number}"]

    if part_name:
        parts.append(f"Part: {part_name}")

    if qna.get("model_number"):
        parts.append(f"Model: {qna['model_number']}")

    if qna.get("question"):
        parts.append(f"Question: {qna['question']}")

    if qna.get("answer"):
        parts.append(f"Answer: {qna['answer']}")

    return "\n".join(parts)
