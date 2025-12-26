"""
Manual test script for live part scraping.

Usage:
    python -m backend.test_scrape_live PS11752778
    python -m backend.test_scrape_live PS99999999
    python -m backend.test_scrape_live INVALID
"""
import sys
import json
from backend.agent_v2.tools import get_tool_map


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m backend.test_scrape_live <PS_NUMBER>")
        print("\nExamples:")
        print("  python -m backend.test_scrape_live PS11752778")
        print("  python -m backend.test_scrape_live PS99999999  # Non-existent part")
        print("  python -m backend.test_scrape_live INVALID     # Invalid format")
        sys.exit(1)

    ps_number = sys.argv[1]

    print(f"\n{'='*60}")
    print(f"Testing live scrape for: {ps_number}")
    print(f"{'='*60}\n")

    # Get the tool from the tool map
    tool_map = get_tool_map()
    scrape_tool = tool_map.get('scrape_part_live')

    if not scrape_tool:
        print("❌ ERROR: scrape_part_live tool not found in registry!")
        sys.exit(1)

    # Call the tool using LangChain's invoke method
    result = scrape_tool.invoke({"ps_number": ps_number})

    print(f"\n{'='*60}")
    print(f"Result:")
    print(f"{'='*60}\n")
    print(json.dumps(result, indent=2))

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"{'='*60}")

    if result.get("error"):
        print(f"❌ ERROR: {result['error']}")
    else:
        print(f"✅ SUCCESS!")
        print(f"  Part Name: {result.get('part_name', 'N/A')}")
        print(f"  PS Number: {result.get('ps_number', 'N/A')}")
        print(f"  Price: ${result.get('part_price', 'N/A')}")
        print(f"  Brand: {result.get('brand', 'N/A')}")
        print(f"  Rating: {result.get('average_rating', 'N/A')} ({result.get('num_reviews', 0)} reviews)")
        print(f"  Scraped Live: {result.get('_scraped_live', False)}")
        print(f"  Q&A Count: {result.get('_qna_count', 0)}")
        print(f"  Stories Count: {result.get('_stories_count', 0)}")
        print(f"  Reviews Count: {result.get('_reviews_count', 0)}")
        print(f"  Compatible Models: {result.get('_model_compatibility_count', 0)}")


if __name__ == "__main__":
    main()
