"""@notice Package entry point for the ANAC explorator project.

@dev The current package baseline now includes:
1. live CKAN resolution with Playwright-backed ANAC access
2. manifest-backed resource download helpers
3. raw schema mapping and comparison
4. vocabulary and dictionary generation
5. parser and cleaning helpers for the first database-oriented pipeline slice
6. DuckDB/Parquet loader helpers plus local SQL query support
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
