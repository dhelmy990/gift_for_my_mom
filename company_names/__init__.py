"""Clean, resolve, and persist company-name aliases."""

from .aliases import AliasSuggestion, suggest_alias
from .cleaning import clean_company_name, normalize_lookup_key
from .repository import AliasMapping, SupabaseAliasRepository
from .service import PreparedAliases, aggregate_resolved_rows, prepare_aliases

__all__ = [
    "AliasMapping",
    "AliasSuggestion",
    "PreparedAliases",
    "SupabaseAliasRepository",
    "aggregate_resolved_rows",
    "clean_company_name",
    "normalize_lookup_key",
    "prepare_aliases",
    "suggest_alias",
]
