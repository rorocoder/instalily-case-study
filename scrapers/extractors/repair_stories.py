"""
Repair Stories Extractor - Extracts customer repair stories from part pages.

Pagination Strategy:
Only extracts first page (~10 stories), sorted by "Most Recent" by default.
These contain real customer repair instructions with difficulty levels and
time estimates - valuable for helping users with their repairs.
"""

import re
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException


def extract_repair_stories(driver):
    """
    Extract customer repair stories from the current product page.

    Args:
        driver: Selenium WebDriver instance (already on the part page)

    Returns:
        list[dict]: List of repair story dictionaries with fields:
            - story_id: str
            - title: str
            - instruction: str
            - author: str
            - difficulty: str
            - repair_time: str
            - helpful_count: int
            - vote_count: int
    """
    stories = []

    try:
        story_containers = driver.find_elements(By.CSS_SELECTOR, "div.repair-story")

        for container in story_containers:
            try:
                story = {}

                # Story ID from voting element
                try:
                    voting_el = container.find_element(By.CSS_SELECTOR, "div.js-repairStoryVoting")
                    story["story_id"] = voting_el.get_attribute("data-id") or ""
                except NoSuchElementException:
                    story["story_id"] = ""

                # Title
                try:
                    title_el = container.find_element(By.CSS_SELECTOR, "div.repair-story__title")
                    story["title"] = title_el.text.strip()
                except NoSuchElementException:
                    story["title"] = ""

                # Instruction text
                try:
                    instruction_el = container.find_element(
                        By.CSS_SELECTOR, "div.repair-story__instruction div.js-searchKeys"
                    )
                    text = instruction_el.text.strip()
                    text = re.sub(r'\.\.\.\s*Read more', '', text)
                    text = re.sub(r'Read less', '', text)
                    story["instruction"] = text.strip()
                except NoSuchElementException:
                    story["instruction"] = ""

                # Author
                try:
                    author_el = container.find_element(
                        By.CSS_SELECTOR, "ul.repair-story__details li div.bold"
                    )
                    story["author"] = author_el.text.strip()
                except NoSuchElementException:
                    story["author"] = ""

                # Difficulty level
                try:
                    details = container.find_elements(By.CSS_SELECTOR, "ul.repair-story__details li")
                    for detail in details:
                        if "Difficulty Level:" in detail.text:
                            full_text = detail.text.strip()
                            story["difficulty"] = full_text.replace("Difficulty Level:", "").strip()
                            break
                    else:
                        story["difficulty"] = ""
                except NoSuchElementException:
                    story["difficulty"] = ""

                # Repair time
                try:
                    details = container.find_elements(By.CSS_SELECTOR, "ul.repair-story__details li")
                    for detail in details:
                        if "Total Repair Time:" in detail.text:
                            full_text = detail.text.strip()
                            story["repair_time"] = full_text.replace("Total Repair Time:", "").strip()
                            break
                    else:
                        story["repair_time"] = ""
                except NoSuchElementException:
                    story["repair_time"] = ""

                # Helpful count and vote count
                try:
                    rating_el = container.find_element(By.CSS_SELECTOR, "div.js-displayRating")
                    helpful = rating_el.get_attribute("data-found-helpful")
                    votes = rating_el.get_attribute("data-vote-count")
                    story["helpful_count"] = int(helpful) if helpful else 0
                    story["vote_count"] = int(votes) if votes else 0
                except (NoSuchElementException, ValueError):
                    story["helpful_count"] = 0
                    story["vote_count"] = 0

                # Only add if we have content
                if story.get("title") or story.get("instruction"):
                    stories.append(story)

            except Exception as e:
                print(f"Error extracting repair story: {e}")
                continue

    except Exception as e:
        print(f"Error finding repair story containers: {e}")

    return stories


def format_for_embedding(story, ps_number, part_name=None):
    """
    Format a repair story into text suitable for vector embedding.

    Args:
        story: Repair story dict from extract_repair_stories
        ps_number: PartSelect part number (e.g., "PS11739091")
        part_name: Optional part name

    Returns:
        str: Formatted text for embedding
    """
    parts = [f"Part ID: {ps_number}"]

    if part_name:
        parts.append(f"Part: {part_name}")

    if story.get("title"):
        parts.append(f"Problem: {story['title']}")

    if story.get("instruction"):
        parts.append(f"Repair Instructions: {story['instruction']}")

    if story.get("difficulty"):
        parts.append(f"Difficulty: {story['difficulty']}")

    if story.get("repair_time"):
        parts.append(f"Time Required: {story['repair_time']}")

    return "\n".join(parts)
