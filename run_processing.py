import os
import json
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple

# --- Import Pipeline Components ---
try:
    # Phase 1-3 Components
    from archon.llms_txt.markdown_processor import MarkdownProcessor

    # Assuming HierarchicalChunker is in chunker.py based on file list
    from archon.llms_txt.chunker import HierarchicalChunker
    from archon.llms_txt.metadata_enricher import MetadataEnricher

    # Phase 4 Components & Utilities
    # Assuming these files exist based on previous main.py imports
    from archon.llms_txt.vector_db.supabase_manager import SupabaseManager
    from archon.llms_txt.vector_db.embedding_manager import OpenAIEmbeddingGenerator
    from archon.llms_txt.vector_db.query_manager import HierarchicalQueryManager
    from archon.llms_txt.utils.env_loader import (
        EnvironmentLoader,
    )  # Used implicitly by managers

    # Phase 5 Components (Retrieval System)
    from archon.llms_txt.retrieval.retrieval_manager import RetrievalManager
    from archon.llms_txt.retrieval.query_processor import QueryProcessor
    from archon.llms_txt.retrieval.ranking import HierarchicalRanker
    from archon.llms_txt.retrieval.response_builder import ResponseBuilder


except ImportError as e:
    print(f"Error importing required Archon components: {e}", flush=True)
    print(
        "Please ensure the Archon package structure is correct and all dependencies are installed.",
        flush=True,
    )
    # Add project root to path if necessary, similar to original script
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        print(
            f"Attempted to add {project_root} to sys.path. Please check imports.",
            flush=True,
        )
    sys.exit(1)


def process_document(
    file_path: str, document_id: Optional[str] = None
) -> Optional[str]:
    """Processes a single markdown document through the full pipeline.

    Parses, chunks, enriches, generates embeddings, and stores the
    hierarchical structure in the Supabase database.

    Args:
        file_path: Path to the markdown file to process.
        document_id: Optional unique identifier for the document. If None,
                     the filename (without extension) is used.

    Returns:
        The document_id used for processing, or None if processing fails.
    """
    print(f"Starting processing for document: {file_path}", flush=True)

    # --- Input Validation ---
    if not os.path.exists(file_path):
        print(f"Error: File not found at {file_path}", flush=True)
        return None
    if not file_path.lower().endswith((".md", ".txt")):  # Allow .txt as well
        print(
            f"Warning: File {file_path} does not have a .md or .txt extension. Attempting to process anyway.",
            flush=True,
        )

    # --- Determine Document ID ---
    effective_document_id = (
        document_id or Path(file_path).stem
    )  # Use Pathlib for cleaner stem extraction
    print(f"Using Document ID: {effective_document_id}", flush=True)

    # --- Initialize Components ---
    # Components will use default EnvironmentLoader unless specific instances are needed
    try:
        print("Initializing components...", flush=True)
        processor = MarkdownProcessor()
        chunker = HierarchicalChunker()
        enricher = MetadataEnricher()
        db = (
            SupabaseManager()
        )  # Uses default env loader path (workbench/env_vars.json relative to root)
        embedder = OpenAIEmbeddingGenerator()  # Uses default env loader path
        print("Components initialized successfully.", flush=True)
        # Perform a quick check of DB connection if desired
        # db._check_tables() # Optional: Check tables exist before proceeding
    except Exception as e:
        print(f"Error initializing components: {e}", flush=True)
        return None  # Cannot proceed if components fail to initialize

    # --- Read File Content ---
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            markdown_text = f.read()
        if not markdown_text.strip():
            print(
                f"Warning: File {file_path} is empty or contains only whitespace.",
                flush=True,
            )
            # Decide whether to proceed or return early for empty files
            return effective_document_id  # Or return None if empty docs shouldn't be processed
    except Exception as e:
        print(f"Error reading file {file_path}: {e}", flush=True)
        return None

    # --- Phase 1: Parse Document ---
    try:
        print("Phase 1: Parsing document...", flush=True)
        parsed_doc = processor.parse_document(markdown_text)
        # Assuming build_hierarchy_tree returns the root node of the tree structure
        doc_tree_root = processor.build_hierarchy_tree(parsed_doc)
        if not doc_tree_root:
            print("Error: Document parsing or tree building failed.", flush=True)
            return None
        print("Phase 1: Document parsed successfully.", flush=True)
    except Exception as e:
        print(f"Error during Phase 1 (Parsing): {e}", flush=True)
        return None

    # --- Phase 2: Create Hierarchical Chunks ---
    try:
        print("Phase 2: Creating hierarchical chunks...", flush=True)
        # Assuming create_chunks takes the tree root and returns a flat list of chunk dicts
        chunks = chunker.create_chunks(doc_tree_root)
        if not chunks:
            print("Warning: No chunks were created from the document.", flush=True)
            # Decide if this is an error or just an empty doc case
            # return None # Or proceed if 0 chunks is valid
        print(f"Phase 2: Created {len(chunks)} chunks.", flush=True)
    except Exception as e:
        print(f"Error during Phase 2 (Chunking): {e}", flush=True)
        return None

    # --- Phase 3: Enrich Chunks with Metadata ---
    try:
        print("Phase 3: Enriching chunks with metadata...", flush=True)
        # Call the correct method which processes all chunks
        enriched_chunks = enricher.process_chunks(
            chunks, doc_tree_root
        )  # Pass chunks and the document tree root
        print("Phase 3: Metadata enrichment complete.", flush=True)
    except Exception as e:
        print(f"Error during Phase 3 (Metadata Enrichment): {e}", flush=True)
        return None

    # --- Phase 4: Prepare Nodes & Pre-process References ---
    print("Phase 4: Preparing nodes and pre-processing references...", flush=True)
    db_nodes_to_insert: List[Dict[str, Any]] = []
    original_id_to_chunk_map: Dict[Any, Dict[str, Any]] = (
        {}
    )  # Map original chunk ID to the full chunk data
    path_to_original_id_map: Dict[str, Any] = {}  # NEW: Map exact path to original ID
    exact_resolved_references: List[Tuple[Any, Any]] = (
        []
    )  # NEW: List of (source_orig_id, target_orig_id)
    paths_needing_fuzzy_lookup: Set[str] = set()  # NEW: Set of paths needing DB lookup

    for chunk in enriched_chunks:
        # Validate essential fields from previous phases
        if "id" not in chunk or "metadata" not in chunk:
            print(
                f"Warning: Skipping chunk due to missing 'id' or 'metadata': {str(chunk)[:100]}...",
                flush=True,
            )
            continue

        original_id = chunk["id"]
        original_id_to_chunk_map[original_id] = (
            chunk  # Store for relationship mapping later
        )

        # --- Map chunk data to database schema ---
        # Extract path safely
        hierarchy_path = chunk["metadata"].get("hierarchy_path", [])
        path_str = (
            " > ".join(map(str, hierarchy_path)) if hierarchy_path else "Unknown Path"
        )

        # Extract other metadata safely
        metadata_payload = {
            k: v
            for k, v in chunk["metadata"].items()
            # Exclude fields that are columns in the main table or internal IDs
            if k
            not in [
                "hierarchy_path",
                "section_type",
                "content_type",
                "document_position",
                "parent_id",
                "child_ids",
                "sibling_ids",
                # Add any other metadata keys that become direct columns
            ]
        }
        # Add original ID to metadata for tracking
        metadata_payload["original_id"] = original_id
        # Add other potentially useful info if not direct columns
        metadata_payload["link_count"] = chunk["metadata"].get("link_count")
        metadata_payload["contains_links"] = chunk["metadata"].get("contains_links")

        node_data = {
            "document_id": effective_document_id,
            "node_type": chunk.get("type", "unknown"),  # Default type if missing
            "title": chunk.get("title"),  # Can be None
            "content": chunk.get("content", ""),  # Default to empty string if missing
            "level": chunk.get("level"),  # Header level, can be None for non-headers
            "path": path_str,
            "section_type": chunk["metadata"].get("section_type", "unknown"),
            "content_type": chunk["metadata"].get("content_type", "text"),
            "document_position": chunk["metadata"].get(
                "document_position"
            ),  # Can be None
            "metadata": metadata_payload,
            # Embedding will be added next
            # parent_id will be added after initial insertion
        }
        db_nodes_to_insert.append(node_data)

        # --- Build path_to_original_id_map ---
        if path_str != "Unknown Path" and original_id:
            path_to_original_id_map[path_str] = original_id

    if not db_nodes_to_insert:
        print("No valid nodes prepared for database insertion.")
        return effective_document_id  # Return ID even if no nodes inserted

    # --- Resolve Exact References & Identify Fuzzy Paths ---
    print(
        "Phase 4: Resolving exact references and identifying fuzzy paths...", flush=True
    )
    for original_id, chunk_data in original_id_to_chunk_map.items():
        source_original_id = original_id
        related_sections = chunk_data.get("metadata", {}).get("related_sections", [])
        for related_path_item in related_sections:  # Renamed variable
            # FIX: Ensure the key used for lookup is always a string
            if isinstance(related_path_item, list):
                path_key_str = " > ".join(map(str, related_path_item))
            elif isinstance(related_path_item, str):
                path_key_str = related_path_item
            else:
                # Handle unexpected types if necessary, e.g., skip or log warning
                print(
                    f"Warning: Skipping unexpected type in related_sections: {type(related_path_item)}",
                    flush=True,
                )
                continue

            target_original_id = path_to_original_id_map.get(path_key_str)
            if target_original_id:
                # Found exact match
                if source_original_id != target_original_id:  # Avoid self-references
                    exact_resolved_references.append(
                        (source_original_id, target_original_id)
                    )
            else:
                # No exact match, need fuzzy lookup (use the string key)
                paths_needing_fuzzy_lookup.add(path_key_str)
    print(f"Found {len(exact_resolved_references)} exact references.", flush=True)
    print(
        f"Identified {len(paths_needing_fuzzy_lookup)} unique paths requiring fuzzy lookup.",
        flush=True,
    )

    # --- Phase 4: Batch Fuzzy Lookups ---
    print("Phase 4: Performing batch fuzzy lookups...", flush=True)
    fuzzy_path_to_nodes_map: Dict[str, List[Dict[str, Any]]] = {}
    fuzzy_lookup_errors = 0
    for (
        path_pattern
    ) in paths_needing_fuzzy_lookup:  # path_pattern is now guaranteed to be a string
        try:
            # Use pattern matching, adjust max_results as needed (e.g., 10)
            # Ensure path_pattern is treated as a string for the f-string
            target_nodes_raw = db.find_nodes_by_path(
                path_pattern=f"%{str(path_pattern)}%", max_results=10
            )

            # FIX: Filter results to include only nodes from the current document
            if target_nodes_raw:
                filtered_target_nodes = [
                    node
                    for node in target_nodes_raw
                    if node.get("document_id") == effective_document_id
                ]
                if (
                    filtered_target_nodes
                ):  # Only store if results remain after filtering
                    fuzzy_path_to_nodes_map[path_pattern] = filtered_target_nodes

        except Exception as e_fuzzy:
            print(
                f"Error during fuzzy lookup for path pattern '{path_pattern}': {e_fuzzy}",
                flush=True,
            )
            fuzzy_lookup_errors += 1
    print(
        f"Batch fuzzy lookups complete. Found nodes for {len(fuzzy_path_to_nodes_map)} paths (after filtering). Errors: {fuzzy_lookup_errors}.",
        flush=True,
    )

    # --- Phase 4: Generate Embeddings ---
    try:
        print(
            f"Phase 4: Generating embeddings for {len(db_nodes_to_insert)} nodes...",
            flush=True,
        )
        # generate_node_embeddings adds 'embedding' key to the dicts in the list
        db_nodes_with_embeddings = embedder.generate_node_embeddings(db_nodes_to_insert)
        print("Phase 4: Embeddings generated.", flush=True)
        # Check how many succeeded
        succeeded_count = sum(
            1
            for node in db_nodes_with_embeddings
            if node.get("metadata", {}).get("embedding_generated")
        )
        if succeeded_count < len(db_nodes_with_embeddings):
            print(
                f"Warning: Embedding generation failed for {len(db_nodes_with_embeddings) - succeeded_count} nodes.",
                flush=True,
            )
    except Exception as e:
        print(f"Error during Phase 4 (Embedding Generation): {e}", flush=True)
        # Decide if we should proceed without embeddings or fail
        return None  # Fail if embeddings are critical

    # --- Phase 4: Insert Nodes into Database ---
    print(
        f"Phase 4: Inserting {len(db_nodes_with_embeddings)} nodes into database...",
        flush=True,
    )
    original_id_to_db_id_map: Dict[Any, int] = (
        {}
    )  # Map original chunk ID to the new database ID
    inserted_count = 0
    failed_count = 0

    # Clear existing nodes for this document ID before inserting new ones
    try:
        print(
            f"Clearing existing nodes for document_id: {effective_document_id}...",
            flush=True,
        )
        deleted_count = db.delete_nodes_by_document_id(effective_document_id)
        print(f"Cleared {deleted_count} existing nodes.", flush=True)
    except Exception as e:
        print(
            f"Error clearing existing nodes for document {effective_document_id}: {e}",
            flush=True,
        )
        # Decide whether to proceed or fail if clearing fails
        # return None # Option: Fail if cleanup is essential

    for node in db_nodes_with_embeddings:
        original_id = node.get("metadata", {}).get("original_id")
        if not original_id:
            print(
                f"Warning: Skipping node insertion due to missing original_id in metadata: {str(node)[:100]}...",
                flush=True,
            )
            failed_count += 1
            continue

        # Only insert nodes for which embedding was successful (or if allowing nodes without embeddings)
        if not node.get("metadata", {}).get("embedding_generated", False):
            print(
                f"Skipping insertion for node {original_id} because embedding generation failed.",
                flush=True,
            )
            failed_count += 1
            continue
            # OR: If allowing nodes without embeddings, remove the check but handle potential DB constraints

        try:
            # Remove temporary metadata before insertion if necessary
            # node_to_insert = node.copy() # Create copy if modifying
            # node_to_insert.get("metadata", {}).pop("embedding_generated", None) # Example cleanup

            db_id = db.insert_node(node)  # Use the prepared node dict directly
            original_id_to_db_id_map[original_id] = db_id
            inserted_count += 1
            # print(f"Inserted node original_id={original_id} -> db_id={db_id}") # Verbose logging
        except Exception as e:
            print(f"Failed to insert node (original_id={original_id}): {e}", flush=True)
            failed_count += 1
            # Optionally: Collect failed nodes for retry or reporting

    print(
        f"Phase 4: Node insertion complete. Inserted: {inserted_count}, Failed: {failed_count}.",
        flush=True,
    )
    if inserted_count == 0 and failed_count > 0:
        print(
            "Error: No nodes were successfully inserted into the database.", flush=True
        )
        return None  # Fail if nothing could be inserted

    # --- Phase 4: Create Relationships (Optimized) ---
    print(
        "Phase 4: Creating relationships (Optimized - Parent Links & References)...",
        flush=True,
    )
    parent_links_set = 0
    references_created = 0
    parent_link_errors = 0
    reference_errors = 0
    inserted_reference_pairs: Set[Tuple[int, int]] = (
        set()
    )  # Track (source_db_id, target_db_id)

    # 1. Set Parent Links (Iterate through inserted nodes)
    print("Setting parent links...", flush=True)
    for original_id, db_id in original_id_to_db_id_map.items():
        chunk_data = original_id_to_chunk_map.get(original_id)
        if not chunk_data:
            continue  # Should not happen if maps are consistent

        original_parent_id = chunk_data.get("metadata", {}).get("parent_id")
        if original_parent_id and original_parent_id in original_id_to_db_id_map:
            db_parent_id = original_id_to_db_id_map[original_parent_id]
            try:
                # Use the dedicated method in SupabaseManager
                db.update_node_parent(node_id=db_id, parent_id=db_parent_id)
                parent_links_set += 1
            except Exception as e:
                print(
                    f"Error setting parent link for node {db_id} (parent: {db_parent_id}): {e}",
                    flush=True,
                )
                parent_link_errors += 1

    # 2. Create References (Combined Pass)
    print("Creating cross-references (exact and fuzzy)...", flush=True)
    # Process Exact Matches
    for source_orig_id, target_orig_id in exact_resolved_references:
        source_db_id = original_id_to_db_id_map.get(source_orig_id)
        target_db_id = original_id_to_db_id_map.get(target_orig_id)

        if source_db_id and target_db_id and source_db_id != target_db_id:
            ref_pair = (source_db_id, target_db_id)
            if ref_pair not in inserted_reference_pairs:
                reference_data = {
                    "source_node_id": source_db_id,
                    "target_node_id": target_db_id,
                    "reference_type": "related_section_exact",  # Mark as exact
                    "strength": 0.9,  # Higher strength for exact?
                }
                try:
                    db.insert_reference(reference_data)
                    references_created += 1
                    inserted_reference_pairs.add(ref_pair)
                except Exception as e_ref:
                    print(
                        f"Failed to insert exact reference from {source_db_id} to {target_db_id}: {e_ref}",
                        flush=True,
                    )
                    reference_errors += 1

    # Process Fuzzy Matches
    for original_id, chunk_data in original_id_to_chunk_map.items():
        source_db_id = original_id_to_db_id_map.get(original_id)
        if not source_db_id:
            continue  # Skip if source node wasn't inserted

        related_sections = chunk_data.get("metadata", {}).get("related_sections", [])
        for related_path_item in related_sections:  # Renamed variable
            # FIX: Ensure the key used for lookup is always a string
            if isinstance(related_path_item, list):
                path_key_str = " > ".join(map(str, related_path_item))
            elif isinstance(related_path_item, str):
                path_key_str = related_path_item
            else:
                # Already handled during the exact match phase, but double-check
                continue

            # Check if this path needed fuzzy lookup and if results were found
            if path_key_str in fuzzy_path_to_nodes_map:
                target_nodes = fuzzy_path_to_nodes_map[
                    path_key_str
                ]  # These are already filtered
                for target_node in target_nodes:
                    target_db_id = target_node.get("id")
                    # Ensure target_db_id exists in the current map (redundant check, but safe)
                    if (
                        target_db_id
                        and target_db_id in original_id_to_db_id_map.values()
                        and target_db_id != source_db_id
                    ):
                        ref_pair = (source_db_id, target_db_id)
                        if ref_pair not in inserted_reference_pairs:
                            reference_data = {
                                "source_node_id": source_db_id,
                                "target_node_id": target_db_id,
                                "reference_type": "related_section_fuzzy",  # Mark as fuzzy
                                "strength": 0.7,  # Lower strength for fuzzy?
                            }
                            try:
                                db.insert_reference(reference_data)
                                references_created += 1
                                inserted_reference_pairs.add(ref_pair)
                            except Exception as e_ref:
                                # Avoid double counting errors if exact failed? No, this is a separate attempt.
                                print(
                                    f"Failed to insert fuzzy reference from {source_db_id} to {target_db_id} (path: {path_key_str}): {e_ref}",
                                    flush=True,
                                )
                                reference_errors += 1

    # Updated final print statement
    print(
        f"Phase 4: Relationship creation complete. Parent links set: {parent_links_set} (Errors: {parent_link_errors}). References created: {references_created} (Errors: {reference_errors}).",
        flush=True,
    )

    # --- Processing Complete ---
    print(f"\nDocument processing complete for: {effective_document_id}", flush=True)
    return effective_document_id


def main():
    """Main entry point for command-line document processing."""
    parser = argparse.ArgumentParser(
        description="Process Markdown/Text documents into a hierarchical vector database using Supabase."
    )
    parser.add_argument(
        "--file",
        "-f",
        required=True,
        help="Path to the single Markdown or Text file to process.",
    )
    parser.add_argument(
        "--id",
        help="Optional unique document ID. Defaults to the filename without extension.",
    )
    parser.add_argument(
        "--query",
        "-q",
        help="Optional test query to run using Hierarchical Search after processing the document.",
    )
    parser.add_argument(
        "--match-count",
        "-k",
        type=int,
        default=5,
        help="Number of results to return for the test query (default: 5).",
    )
    parser.add_argument(
        "--context-depth",
        "-d",
        type=int,
        default=2,
        help="Context depth for hierarchical search test query (default: 2).",
    )
    parser.add_argument(
        "--test-query",
        help="Optional test query to run using the full RetrievalManager after processing.",
    )

    args = parser.parse_args()

    # --- Process the Document ---
    processed_doc_id = process_document(args.file, args.id)

    if not processed_doc_id:
        print("\nDocument processing failed.", flush=True)
        sys.exit(1)  # Exit with error code if processing failed

    # --- Run Test Query (Optional) ---
    if args.query:
        print(f"\n--- Running Test Query ---", flush=True)
        print(f"Query: '{args.query}'", flush=True)
        print(f"Match Count (k): {args.match_count}", flush=True)
        print(f"Context Depth (d): {args.context_depth}", flush=True)

        try:
            query_manager = HierarchicalQueryManager()  # Initialize fresh manager
            results = query_manager.hierarchical_search(
                query=args.query,
                match_count=args.match_count,
                context_depth=args.context_depth,
                document_id=processed_doc_id,  # Filter query by the processed document ID
            )

            print(
                f"\nFound {len(results)} results for document '{processed_doc_id}':",
                flush=True,
            )
            if not results:
                print("(No matching results found)", flush=True)

            for i, result_cluster in enumerate(results):
                main_node = result_cluster.get("main_node", {})
                similarity = result_cluster.get("similarity", 0)  # Use 0 if missing

                print(
                    f"\n--- Result {i+1} (Similarity: {similarity:.4f}) ---", flush=True
                )
                print(f"  ID: {main_node.get('id')}", flush=True)
                print(f"  Path: {main_node.get('path')}", flush=True)
                print(f"  Title: {main_node.get('title')}", flush=True)

                # Display snippet of content
                content = main_node.get("content", "")
                content_snippet = (
                    (content[:150] + "...") if len(content) > 150 else content
                )
                print(
                    f"  Content Snippet: {content_snippet.replace(chr(10), ' ')}",
                    flush=True,
                )  # Replace newlines for readability

                # Display Parents
                parents = result_cluster.get("parents", [])
                if parents:
                    parent_paths = [
                        p.get("path", "Unknown Parent Path") for p in parents
                    ]
                    print(
                        f"  Parents: {' -> '.join(reversed(parent_paths))}", flush=True
                    )  # Show root first
                else:
                    print("  Parents: (None)", flush=True)

                # Display Children (if included and exist)
                children = result_cluster.get("children", [])
                if children:
                    print(f"  Children ({len(children)}):", flush=True)
                    for child in children[:3]:  # Show first few children
                        child_title = child.get(
                            "title", f"Child Node {child.get('id')}"
                        )
                        print(
                            f"    - {child_title} (ID: {child.get('id')})", flush=True
                        )
                    if len(children) > 3:
                        print("    ...", flush=True)
                # else: print("  Children: (None)") # Optional: uncomment if you want to explicitly state no children

                # Display References (if included and exist)
                references = result_cluster.get("references", [])
                if references:
                    print(f"  References ({len(references)}):")
                    for ref in references[:3]:  # Show first few references
                        ref_type = ref.get("reference_type", "related")
                        target_id = ref.get("target_node_id")
                        # Ideally, fetch target node title/path here if needed, but keep it simple for now
                        print(f"    - Type: {ref_type}, Target ID: {target_id}")
                    if len(references) > 3:
                        print("    ...")
                # else: print("  References: (None)") # Optional

        except Exception as e:
            print(f"\nError running test query: {e}")
            # Optionally re-raise or exit differently on query failure
            # sys.exit(1)

    # --- Run Full Retrieval Test Query (Optional - Phase 5 Integration) ---
    if args.test_query:
        print(f"\n--- Running Full Retrieval Test Query ---")
        print(f"Test Query: '{args.test_query}'")

        try:
            # Instantiate retrieval components (using placeholders as needed)
            print("Initializing retrieval components...")
            query_processor = QueryProcessor()
            # Assuming HierarchicalRanker doesn't need complex init for this test
            ranker = HierarchicalRanker()
            response_builder = ResponseBuilder()
            # Instantiate SupabaseManager to provide access to the stored data
            # Note: RetrievalManager._perform_search needs to be implemented to use this client
            db_client = SupabaseManager()  # Use the existing DB manager
            retrieval_manager = RetrievalManager(
                search_client=db_client,  # Pass the actual DB client
                query_processor=query_processor,
                ranker=ranker,
                response_builder=response_builder,
            )
            print("Retrieval components initialized.")

            # Execute the retrieval process
            print("Executing retrieval...")
            retrieval_results = retrieval_manager.retrieve(args.test_query)

            # Print the results (basic output)
            print("\n--- Retrieval Results ---")
            if retrieval_results:
                # Basic print - adjust formatting as needed based on actual results structure
                print(json.dumps(retrieval_results, indent=2))
            else:
                print("(No results returned from retrieval manager)")

        except Exception as e:
            print(f"\nError running full retrieval test query: {e}")
            # Optionally re-raise or exit differently
            # sys.exit(1)


if __name__ == "__main__":
    main()
