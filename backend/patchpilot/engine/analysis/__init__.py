"""Static code analysis for API client repositories."""

from .matcher import Location, MatchedChange, match_changes, path_prefixes, summarise
from .model import CallSite, FieldAccess, LiteralRef, ScanResult
from .scanner import RepoScanner, scan_repository

__all__ = [
    "CallSite",
    "FieldAccess",
    "LiteralRef",
    "Location",
    "MatchedChange",
    "RepoScanner",
    "ScanResult",
    "match_changes",
    "path_prefixes",
    "scan_repository",
    "summarise",
]
