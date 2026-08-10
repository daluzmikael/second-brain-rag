import argparse
from pathlib import Path

from app.ingest import ingest_directory
from app.search import search
from app.synthesize import answer


def main() -> None:
    parser = argparse.ArgumentParser(prog="second-brain-rag")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Chunk, embed, and store documents")
    ingest_parser.add_argument("path", nargs="?", default="data/docs")

    search_parser = subparsers.add_parser("search", help="Semantic search only, no LLM call")
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=5)

    ask_parser = subparsers.add_parser("ask", help="Search + synthesize an answer")
    ask_parser.add_argument("query")
    ask_parser.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args()

    if args.command == "ingest":
        ingest_directory(Path(args.path))

    elif args.command == "search":
        results = search(args.query, top_k=args.top_k)
        for i, r in enumerate(results, start=1):
            print(f"[{i}] {r.source_path} (similarity={r.similarity:.3f})")
            print(f"    {r.content[:200].strip()}...\n")

    elif args.command == "ask":
        results = search(args.query, top_k=args.top_k)
        print(answer(args.query, results))
        print("\nSources:")
        for i, r in enumerate(results, start=1):
            print(f"  [{i}] {r.source_path}")


if __name__ == "__main__":
    main()
