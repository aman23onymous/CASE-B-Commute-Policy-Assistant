import argparse
import asyncio
import sys

from app.answer import AnswerService
from app.config import settings
from app.db import get_db_connection
from app.embedder import get_embedder
from app.llm import get_llm_provider
from app.retrieval import Retriever
from app.schemas import Principal
from app.vector_store import ChromaVectorStore
from scripts.seed import seed_database


async def query_policy(company_id: str, question: str):
    # Ensure database is seeded
    seed_database(reset=False)

    conn = get_db_connection(settings.SQLITE_PATH)
    vector_store = ChromaVectorStore(settings.CHROMA_PATH)
    embedder = get_embedder()
    retriever = Retriever(vector_store, embedder)
    llm = get_llm_provider()
    service = AnswerService(conn, retriever, llm)

    principal = Principal(
        user_id=f"{company_id}_cli_user",
        company_id=company_id,
        role="employee",
        display_name=f"{company_id.capitalize()} Employee",
    )

    print("\n" + "=" * 60)
    print(f" COMPANY:  {company_id.upper()}")
    print(f" QUESTION: {question}")
    print("=" * 60)

    resp = await service.ask(principal, question)

    print(f"\nSTATUS: {resp.status.upper()}")
    print("-" * 60)
    print(f"ANSWER:\n{resp.answer}")
    print("-" * 60)

    if resp.citations:
        print(f"CITATIONS ({len(resp.citations)}):")
        for i, cit in enumerate(resp.citations, 1):
            print(f"  [{i}] {cit.document_title} (v{cit.version}) — §{cit.section_id} {cit.section_title}")
            if cit.excerpt:
                print(f"      Excerpt: \"{cit.excerpt}\"")
        print("-" * 60)

    print(f"TIMING: Total: {resp.timing_ms.total}ms | Retrieval: {resp.timing_ms.retrieval}ms | LLM: {resp.timing_ms.llm}ms")
    print("=" * 60 + "\n")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Ask questions to Commute Policy Assistant via CLI")
    parser.add_argument("--company", choices=["aurora", "bluepeak", "cedar"], default="aurora", help="Company context")
    parser.add_argument("question", nargs="?", default=None, help="Policy question text")

    args = parser.parse_args()

    if not args.question:
        # Interactive mode
        print("\n=== Commute Policy Assistant Interactive CLI ===")
        print("Available companies: aurora, bluepeak, cedar")
        company = input("Select company (default: aurora): ").strip().lower() or "aurora"
        if company not in ["aurora", "bluepeak", "cedar"]:
            print("Invalid company. Defaulting to aurora.")
            company = "aurora"

        while True:
            try:
                q = input(f"\n[{company.upper()}] Ask a question (or 'exit' to quit): ").strip()
                if not q or q.lower() in ("exit", "quit", "q"):
                    break
                asyncio.run(query_policy(company, q))
            except (KeyboardInterrupt, EOFError):
                break
    else:
        asyncio.run(query_policy(args.company, args.question))


if __name__ == "__main__":
    main()
