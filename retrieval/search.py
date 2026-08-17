import os
import sys

try:
    from retrieval.knowledge import KnowledgeStore
except ImportError:  # allow running as a plain script
    sys.path.insert(0, os.path.dirname(__file__))
    from knowledge import KnowledgeStore

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge")


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python retrieval/search.py <query>")
        return

    query = " ".join(sys.argv[1:])

    store = KnowledgeStore().load(KNOWLEDGE_DIR)
    results = store.search(query)

    print(f"\nQuery: {query}")
    print(f"Knowledge items: {len(store.all())}")
    print(f"Matches: {len(results)}")
    if store.errors:
        print(f"Load errors: {len(store.errors)}")
    print()

    for score, item in results:
        print(f"[score={score}] {item['name']} ({item['type']})")
        print(f"source: {item['_source']}")
        print(f"description: {item.get('description', '')}")
        print()


if __name__ == "__main__":
    main()
