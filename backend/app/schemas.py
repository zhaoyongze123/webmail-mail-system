from typing import Any

from pydantic import BaseModel, Field

DEFAULT_SETTINGS_PAGE_SIZE = 30
DEFAULT_SETTINGS_MARK_READ_ON_OPEN = True
DEFAULT_SETTINGS_REPLY_QUOTE_POSITION = "bottom"
DEFAULT_SETTINGS_LANGUAGE = "zh-CN"
DEFAULT_SETTINGS_TIMEZONE = "Asia/Shanghai"


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiResponse(BaseModel):
    success: bool
    data: Any = None
    error: ApiError | None = None
    request_id: str


class SignatureCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=500000)
    is_default: bool = False


class SignatureUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=500000)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ChangePasswordResponse(BaseModel):
    password_updated: bool


class UserSettingsPreferences(BaseModel):
    page_size: int
    mark_read_on_open: bool
    language: str = DEFAULT_SETTINGS_LANGUAGE
    timezone: str = DEFAULT_SETTINGS_TIMEZONE
    reply_quote_position: str = Field(default=DEFAULT_SETTINGS_REPLY_QUOTE_POSITION)


class SystemSettingsPreferences(BaseModel):
    page_size: int = DEFAULT_SETTINGS_PAGE_SIZE
    mark_read_on_open: bool = DEFAULT_SETTINGS_MARK_READ_ON_OPEN
    language: str = DEFAULT_SETTINGS_LANGUAGE
    timezone: str = DEFAULT_SETTINGS_TIMEZONE
    reply_quote_position: str = Field(default=DEFAULT_SETTINGS_REPLY_QUOTE_POSITION)


class UserProfileSettings(BaseModel):
    display_name: str = ""
    profile_title: str = ""
    avatar_url: str = ""
    bio: str = ""


class ThemeSettings(BaseModel):
    mode: str = "light"


class SettingsPreferences(BaseModel):
    system: SystemSettingsPreferences = Field(default_factory=SystemSettingsPreferences)
    user: UserProfileSettings = Field(default_factory=UserProfileSettings)
    theme: ThemeSettings = Field(default_factory=ThemeSettings)


class SignatureResponse(BaseModel):
    id: str
    name: str
    content: str
    is_default: bool
    created_at: str
    updated_at: str


class ContactTagItem(BaseModel):
    name: str
    created_at: str


class ContactResponse(BaseModel):
    id: str
    email: str
    display_name: str | None = None
    group_name: str | None = None
    company: str | None = None
    phone: str | None = None
    notes: str | None = None
    is_favorite: bool = False
    is_blacklisted: bool = False
    is_whitelisted: bool = False
    source: str
    use_count: int
    last_used_at: str | None = None
    created_at: str
    updated_at: str
    tags: list[ContactTagItem] = Field(default_factory=list)


class ContactCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=255)
    group_name: str | None = Field(default=None, max_length=100)
    company: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)
    is_favorite: bool = False
    is_blacklisted: bool = False
    is_whitelisted: bool = False
    tags: list[str] = Field(default_factory=list)


class ContactUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    group_name: str | None = Field(default=None, max_length=100)
    company: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)
    is_favorite: bool | None = None
    is_blacklisted: bool | None = None
    is_whitelisted: bool | None = None
    tags: list[str] | None = None


class ContactListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[ContactResponse]


class ContactSearchResponse(BaseModel):
    query: str
    contacts: list[ContactResponse | dict[str, str]]


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=512)


class FolderRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    new_name: str = Field(min_length=1, max_length=512)


class FolderDeleteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=512)


class FolderOperationResponse(BaseModel):
    folder: str
    new_name: str | None = None
    deleted: bool | None = None
