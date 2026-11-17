"""Third-party integrations."""

from .github import GitHubClient, GitHubError, parse_repo

__all__ = ["GitHubClient", "GitHubError", "parse_repo"]
