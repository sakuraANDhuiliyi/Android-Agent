from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceFile(Model):
    path: str = Field(min_length=1, max_length=180)
    content: str = Field(max_length=262144)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (value != str(path) or path.is_absolute() or "\\" in value or ":" in value
                or any(part in {"..", ".git", ".android-agent"} or part.startswith(".") for part in path.parts)
                or path.suffix.lower() not in {".kt", ".java", ".xml", ".txt", ".md"}
                or any(ord(char) < 32 for char in value)):
            raise ValueError("源码必须是工作区内的普通 Kotlin/Java/XML/文本相对路径")
        return value

    @field_validator("content")
    @classmethod
    def text_size(cls, value: str) -> str:
        if "\x00" in value or len(value.encode("utf-8")) > 262144:
            raise ValueError("单个源码文件不能超过 256 KiB，且不能包含二进制内容")
        return value


class Reference(Model):
    title: str = Field(min_length=1, max_length=120)
    url: str = Field(max_length=2000)

    @field_validator("url")
    @classmethod
    def link(cls, value: str) -> str:
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("参考链接必须是无凭据的 HTTPS 地址")
        return value


class CreativeContent(Model):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=10000)
    category_id: str = Field(default="component", pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    tags: list[str] = Field(default_factory=list, max_length=12)
    ui_stack: Literal["compose", "xml", "mixed"] = "compose"
    min_sdk: int = Field(default=24, ge=21, le=100)
    dependencies: list[str] = Field(default_factory=list, max_length=30)
    integration: str = Field(default="", max_length=10000)
    license: str = Field(default="", max_length=120)
    attribution: str = Field(default="", max_length=1000)
    references: list[Reference] = Field(default_factory=list, max_length=10)
    files: list[SourceFile] = Field(default_factory=list, max_length=30)
    cover_asset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    native_preview_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")

    @field_validator("title")
    @classmethod
    def title_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("标题不能为空")
        return value.strip()

    @field_validator("tags", "dependencies")
    @classmethod
    def short_strings(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 200 or "\n" in item for item in values):
            raise ValueError("标签和依赖必须是非空短文本")
        return list(dict.fromkeys(item.strip() for item in values))

    @model_validator(mode="after")
    def source_budget(self) -> "CreativeContent":
        if len({file.path.casefold() for file in self.files}) != len(self.files):
            raise ValueError("源码文件路径重复")
        if sum(len(file.content.encode("utf-8")) for file in self.files) > 1024 * 1024:
            raise ValueError("一个创意的源码总量不能超过 1 MiB")
        return self


class Category(Model):
    id: str
    label: str
    sort_order: int = 0
    enabled: bool = True
    row_version: int = 1


class CreativeCard(Model):
    id: str
    legacy_id: str | None = None
    revision_id: str
    version_no: int
    title: str
    summary: str
    category_id: str
    category_label: str
    tags: list[str]
    origin: Literal["official", "community"]
    author_name: str
    ui_stack: str
    min_sdk: int
    cover_asset_id: str | None = None
    native_preview_id: str | None = None
    source_hash: str | None = None
    content_hash: str
    featured: bool
    sort_order: int
    published_at: float | None
    verification: Literal["not_verified"] = "not_verified"


class CreativeDetail(CreativeCard):
    content: CreativeContent


class CatalogPage(Model):
    items: list[CreativeCard]
    categories: list[Category]
    next_cursor: str | None
    generation: int


class RevisionInfo(Model):
    id: str
    version_no: int
    state: str
    content_hash: str
    created_at: float
    published_at: float | None


class AdminItem(Model):
    id: str
    legacy_id: str | None
    origin: str
    distribution_state: str
    row_version: int
    featured: bool
    sort_order: int
    published_revision_id: str | None
    revision_id: str
    content: CreativeContent
    revisions: list[RevisionInfo]
    author_name: str = "Android Agent"
    owner_user_id: str | None = None


class VersionAction(Model):
    expected_version: int = Field(ge=1)
    revision_id: str | None = None
    reason: str = Field(default="", max_length=1000)


class SaveDraft(Model):
    expected_version: int = Field(ge=1)
    content: CreativeContent


class Placement(Model):
    expected_version: int = Field(ge=1)
    featured: bool
    sort_order: int = Field(ge=-10000, le=10000)


class SaveCategory(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    label: str = Field(min_length=1, max_length=40)
    sort_order: int = Field(default=0, ge=-10000, le=10000)
    enabled: bool = True
    expected_version: int | None = Field(default=None, ge=1)


def source_hash(content: CreativeContent) -> str | None:
    if len(content.files) != 1:
        return None
    return hashlib.sha256(content.files[0].content.encode()).hexdigest()


class CreateSubmission(Model):
    client_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")
    content: CreativeContent


class SubmitRevision(VersionAction):
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    sharing_confirmed: Literal[True]


class ReviewDecision(VersionAction):
    decision: Literal["claim", "approve", "changes_requested", "reject"]
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    feedback: str = Field(default="", max_length=2000)
    private_note: str = Field(default="", max_length=2000)


class UploadRequest(Model):
    client_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0, le=1536 * 1024)


class ReadNotifications(Model):
    through_id: int = Field(ge=1)


class AuthorFeedback(Model):
    revision_id: str
    decision: str
    feedback: str
    created_at: float


class AuthorItem(Model):
    id: str
    origin: Literal["community"]
    author_name: str
    distribution_state: str
    row_version: int
    published_revision_id: str | None
    revision_id: str
    content: CreativeContent
    revisions: list[RevisionInfo]
    feedback: list[AuthorFeedback]


class CreatedAuthorItem(AuthorItem):
    create_content_hash: str


class AuthorSummary(Model):
    id: str
    distribution_state: str
    row_version: int
    updated_at: float
    version_no: int
    state: str
    title: str


class AuthorList(Model):
    items: list[AuthorSummary]


class CreatorIdentity(Model):
    user_id: str
    display_name: str
    schema_version: Literal[1]
    categories: list[Category]
    submissions_enabled: bool


class UploadSession(Model):
    id: str
    owner_user_id: str
    client_id: str
    sha256: str
    size: int
    state: Literal["pending", "complete"]
    asset_id: str | None
    created_at: float


class CreativeNotification(Model):
    id: int
    creative_id: str
    revision_id: str | None
    kind: str
    message: str
    created_at: float
    read_at: float | None


class NotificationPage(Model):
    items: list[CreativeNotification]
    next_after: int


class ReportRequest(Model):
    client_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")
    reason: Literal["copyright", "privacy", "malware", "misleading", "other"]
    details: str = Field(min_length=5, max_length=2000)

    @field_validator("details")
    @classmethod
    def report_details(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("请至少用 5 个字符说明问题")
        return value


class ReportReceipt(Model):
    id: str
    creative_id: str
    revision_id: str
    reason: str
    state: str
    row_version: int
    resolution: str
    created_at: float
    updated_at: float


class ReportDecision(Model):
    expected_version: int = Field(ge=1)
    expected_creative_version: int | None = Field(default=None, ge=1)
    decision: Literal["claim", "resolve", "dismiss", "block"]
    resolution: str = Field(default="", max_length=2000)
    private_note: str = Field(default="", max_length=2000)


class FavoriteList(Model):
    creative_ids: list[str]


class CreativeMetrics(Model):
    published: int
    community_published: int
    pending_reviews: int
    open_reports: int
    favorites: int
    submissions_24h: int
