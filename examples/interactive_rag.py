#!/usr/bin/env python3

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from kamiwaza_client import KamiwazaClient


# ------------------------------
# Utilities
# ------------------------------

def list_markdown_files(root: Path, max_files: int = 1000) -> List[Path]:
    files: List[Path] = []
    if root.is_file():
        if root.suffix.lower() == ".md":
            return [root]
        return []
    for p in root.rglob("*.md"):
        # Skip notebook checkpoints
        if ".ipynb_checkpoints" in p.parts:
            continue
        files.append(p)
        if len(files) >= max_files:
            break
    return files


def safe_read_utf8_window(path: Path, start: int, length: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(max(0, start))
            data = f.read(max(0, length))
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="ignore")
    except Exception as exc:
        return f"<error reading bytes: {exc}>"


def rechunk_get_chunk_text(
    client: KamiwazaClient,
    model: str,
    provider_type: str,
    source_path: Path,
    offset: int,
    chunk_size: int,
    overlap: int,
) -> Optional[str]:
    """Re-run chunking with the same params and return exact chunk text by offset."""
    try:
        text = source_path.read_text(encoding="utf-8")
    except Exception as exc:
        return None

    embedder = client.embedding.get_embedder(
        model=model,
        provider_type=provider_type,
    )
    resp = embedder.chunk_text(
        text=text,
        max_length=chunk_size,
        overlap=overlap,
        return_metadata=True,
    )

    chunks = resp.chunks if hasattr(resp, "chunks") else resp.get("chunks", [])
    offsets = resp.offsets if hasattr(resp, "offsets") else resp.get("offsets", [])

    for ch_text, ch_off in zip(chunks, offsets or []):
        if int(ch_off) == int(offset):
            return ch_text
    return None


# ------------------------------
# Ingestion (catalog, chunk, embed, insert)
# ------------------------------

def create_catalog_entries(client: KamiwazaClient, files: List[Path]) -> Dict[Path, str]:
    mapping: Dict[Path, str] = {}
    for fp in files:
        ds = client.catalog.create_dataset(
            dataset_name=str(fp.resolve()),
            platform="file",
            environment="PROD",
            description=f"RAG demo: {fp.name}",
        )
        if not ds.urn:
            raise RuntimeError(f"No URN returned for dataset {fp}")
        mapping[fp] = ds.urn
        logging.info(f"Cataloged: {fp.name} | URN: {ds.urn}")
    return mapping


def insert_document(
    client: KamiwazaClient,
    file_path: Path,
    dataset_urn: str,
    collection_name: str,
    embedder_model: str,
    chunk_size: int,
    chunk_overlap: int,
    provider_type: str,
) -> int:
    embedder = client.embedding.get_embedder(
        model=embedder_model,
        provider_type=provider_type,
    )

    text = file_path.read_text(encoding="utf-8")

    # Chunk with offsets
    chunk_resp = embedder.chunk_text(
        text=text,
        max_length=chunk_size,
        overlap=chunk_overlap,
        return_metadata=True,
    )
    chunks: List[str] = chunk_resp.chunks if hasattr(chunk_resp, "chunks") else chunk_resp["chunks"]
    offsets: List[int] = chunk_resp.offsets if hasattr(chunk_resp, "offsets") else chunk_resp.get("offsets", [])

    if not chunks:
        logging.warning(f"No chunks produced for {file_path}")
        return 0

    # Embed
    vectors = embedder.embed_chunks(chunks)

    # Metadata with autofields only
    metadata: List[Dict[str, Any]] = []
    for i, byte_offset in enumerate(offsets or [0] * len(chunks)):
        metadata.append(
            {
                "model_name": embedder_model,
                "source": str(file_path.resolve()),
                "catalog_urn": dataset_urn,
                "offset": int(byte_offset),
                "filename": file_path.name,
            }
        )

    # Insert
    client.vectordb.insert(
        vectors=vectors,
        metadata=metadata,
        collection_name=collection_name,
        field_list=None,
    )

    return len(chunks)


# ------------------------------
# RAG Query and Generation
# ------------------------------

def retrieve_topk(
    client: KamiwazaClient,
    embedder_model: str,
    provider_type: str,
    collection_name: str,
    query: str,
    k: int,
) -> List[Dict[str, Any]]:
    embedder = client.embedding.get_embedder(
        model=embedder_model,
        provider_type=provider_type,
    )
    q_vec = embedder.create_embedding(query).embedding
    hits = client.vectordb.search(
        query_vector=q_vec,
        collection_name=collection_name,
        limit=k,
        output_fields=["source", "offset", "filename", "catalog_urn", "model_name"],
    )
    # Normalize structure
    results: List[Dict[str, Any]] = []
    for hit in hits:
        score = getattr(hit, "score", None)
        if score is None and isinstance(hit, dict):
            score = hit.get("score", 0.0)
        meta = getattr(hit, "metadata", None) or (hit.get("metadata") if isinstance(hit, dict) else {})
        results.append({"score": float(score or 0.0), "metadata": meta})
    return results


def build_context(
    client: KamiwazaClient,
    hits: List[Dict[str, Any]],
    embedder_model: str,
    provider_type: str,
    chunk_size: int,
    overlap: int,
    preview_mode: str = "rechunk",
    pre_bytes: int = 500,
    post_bytes: int = 2000,
    max_total_chars: int = 8000,
) -> Tuple[str, List[str]]:
    """Return (context_text, citations)."""
    context_parts: List[str] = []
    citations: List[str] = []
    total_chars = 0

    for i, item in enumerate(hits, 1):
        meta = item.get("metadata", {})
        source = meta.get("source")
        offset = int(meta.get("offset", 0))
        filename = meta.get("filename", "")

        if not source or not Path(source).exists():
            continue
        source_path = Path(source)

        if preview_mode == "rechunk":
            snippet = rechunk_get_chunk_text(
                client=client,
                model=embedder_model,
                provider_type=provider_type,
                source_path=source_path,
                offset=offset,
                chunk_size=chunk_size,
                overlap=overlap,
            )
            if snippet is None:
                snippet = safe_read_utf8_window(source_path, max(0, offset - pre_bytes), pre_bytes + post_bytes)
        elif preview_mode == "around":
            snippet = safe_read_utf8_window(source_path, max(0, offset - pre_bytes), pre_bytes + post_bytes)
        else:  # from
            snippet = safe_read_utf8_window(source_path, offset, post_bytes)

        header = f"[Source {i}] {filename} @ offset {offset}"
        part = f"{header}\n---\n{snippet}\n"

        # Cap total context size
        if total_chars + len(part) > max_total_chars:
            break
        context_parts.append(part)
        citations.append(f"{filename}:{offset}")
        total_chars += len(part)

    return "\n\n".join(context_parts), citations


def pick_first_active_deployment(client: KamiwazaClient) -> Optional[str]:
    try:
        deployments = client.serving.list_active_deployments()
    except Exception:
        deployments = []
    if not deployments:
        return None
    # Use the first deployment's model name
    dep = deployments[0]
    # In SDK objects, the field is commonly m_name in notebooks
    model_name = getattr(dep, "m_name", None) or getattr(dep, "name", None)
    return model_name


def generate_with_llm(client: KamiwazaClient, model_name: str, question: str, context: str) -> str:
    openai_client = client.openai.get_client(model_name)

    system_prompt = (
        "You are a helpful assistant. Answer the user's question using ONLY the provided context. "
        "If the answer cannot be derived from the context, say you do not know. "
        "When possible, cite sources as filename:offset."
    )

    user_content = f"Question:\n{question}\n\nContext:\n{context}"

    try:
        resp = openai_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            timeout=600,
        )
        return resp.choices[0].message.content
    except Exception as exc:
        return f"<LLM generation error: {exc}>"


# ------------------------------
# Main
# ------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end RAG demo with Kamiwaza SDK (offsets-only)")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("KAMIWAZA_API_URL", "http://localhost:7777/api/"),
        help="Kamiwaza API base URL",
    )
    parser.add_argument(
        "--data-path",
        default=str(Path(__file__).parent / "sdk"),
        help="Path to directory or file with .md docs (default: notebooks/sdk)",
    )
    parser.add_argument("--chunk-size", type=int, default=600, help="Chunk token length")
    parser.add_argument("--overlap", type=int, default=102, help="Token overlap")
    parser.add_argument(
        "--model",
        default="BAAI/bge-base-en-v1.5",
        help="Embedding model",
    )
    parser.add_argument(
        "--provider",
        default="huggingface_embedding",
        help="Embedding provider type",
    )
    parser.add_argument("--k", type=int, default=5, help="Top K chunks to retrieve")
    parser.add_argument(
        "--preview-mode",
        choices=["rechunk", "around", "from"],
        default="rechunk",
        help="How to display chunk content in logs",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    client = KamiwazaClient(base_url=args.api_url)

    # Build collection name
    collection_name = f"SDKRAG_{int(time.time())}"

    # Prepare data
    data_root = Path(args.data_path).resolve()
    if not data_root.exists():
        logging.error(f"Data path not found: {data_root}")
        sys.exit(1)

    files = list_markdown_files(data_root)
    if not files:
        logging.error(f"No markdown files found in {data_root}")
        sys.exit(1)

    logging.info(f"Found {len(files)} docs. Target collection: {collection_name}")

    # Collection handling: drop if exists
    try:
        existing = client.vectordb.list_collections()
        if collection_name in existing:
            logging.info(f"Dropping existing collection {collection_name}")
            client.vectordb.drop_collection(collection_name)
    except Exception:
        pass

    # Catalog
    logging.info("Creating catalog datasets...")
    path_to_urn = create_catalog_entries(client, files)

    # Ingest
    total = 0
    for fp in files:
        logging.info(f"Embedding + inserting: {fp.name}")
        try:
            cnt = insert_document(
                client=client,
                file_path=fp,
                dataset_urn=path_to_urn[fp],
                collection_name=collection_name,
                embedder_model=args.model,
                chunk_size=args.chunk_size,
                chunk_overlap=args.overlap,
                provider_type=args.provider,
            )
            logging.info(f"Inserted {cnt} chunks from {fp.name}")
            total += cnt
        except Exception as exc:
            logging.error(f"Failed to process {fp}: {exc}")

    logging.info(f"Completed ingestion. Collection {collection_name} contains ~{total} chunks (across files)")

    # Interactive loop
    print("\nRAG setup complete. Enter a query to run retrieval + generation. Press Enter to exit.\n")

    while True:
        try:
            query = input("Query> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        if not query:
            print("Done.")
            break

        logging.info("Embedding query and searching...")
        hits = retrieve_topk(
            client=client,
            embedder_model=args.model,
            provider_type=args.provider,
            collection_name=collection_name,
            query=query,
            k=args.k,
        )

        if not hits:
            print("No relevant chunks found. Try another query.")
            continue

        # Log previews
        for i, item in enumerate(hits, 1):
            meta = item.get("metadata", {})
            source = meta.get("source")
            offset = int(meta.get("offset", 0))
            score = item.get("score", 0.0)
            print(f"Result {i}: score={score:.4f} file={Path(source).name if source else 'N/A'} offset={offset}")

        logging.info("Reconstructing context text from offsets...")
        context, citations = build_context(
            client=client,
            hits=hits,
            embedder_model=args.model,
            provider_type=args.provider,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            preview_mode=args.preview_mode,
        )

        # Pick first active deployment
        model_name = pick_first_active_deployment(client)
        if not model_name:
            print("No active deployments found; cannot run LLM generation. Displaying context preview only.\n")
            print(context)
            continue

        print(f"Using deployed model: {model_name}")
        logging.info("Calling LLM with context + question...")
        answer = generate_with_llm(client, model_name=model_name, question=query, context=context)

        print("\n===== RAG ANSWER =====")
        print(answer)
        print("======================\n")


if __name__ == "__main__":
    main()
