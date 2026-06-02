"""Retrieval pipeline (DESIGN.md §9): adaptive gate + HyDE (full mode),
parallel vector + BM25 search, graph expansion, RRF + MMR fusion, tiered
token-budget assembly.

Step 0 implements a minimal path: BM25 search + naive assembly. Vector search
activates once the index is trained (DESIGN.md §7).
"""
