"""
Type definitions for TECMIS Notices
"""

from typing import TypedDict, Literal, Optional


class Notice(TypedDict):
    """TECMIS Notice structure"""
    id: str  # Stable identifier (notice-XXX or file-XXX)
    section: Literal["recent", "previous"]
    no: int | str  # Notice number
    posted_at: str  # Format: "YYYY-MM-DD/HH:MM"
    title: str
    url: str  # Download or view URL
    file: Optional[str]  # Filename if it's a direct file download


class RedisNoticePayload(TypedDict):
    """Payload structure for Redis API"""
    id: str  # Redis ID (tecmis-{notice.id})
    title: str  # Formatted title with "TECMIS:" prefix
    body: str  # Markdown body with notice details
    type: Literal["tecmis"]
    createdAt: str  # ISO 8601 timestamp
    updatedAt: str  # ISO 8601 timestamp
    done: bool
    seen: bool
    source: Literal["tecmis-scraper"]
    
    
class GitHubNotice(Notice):
    """Extended Notice with GitHub URL for attachments"""
    github_url: Optional[str]  # GitHub raw URL for the file
