"""前后端接口共享的 Pydantic 数据模型。"""

from typing import Any

from pydantic import BaseModel, Field

DEFAULT_SETTINGS_PAGE_SIZE = 30
DEFAULT_SETTINGS_MARK_READ_ON_OPEN = True
DEFAULT_SETTINGS_REPLY_QUOTE_POSITION = "bottom"
DEFAULT_SETTINGS_LANGUAGE = "zh-CN"
DEFAULT_SETTINGS_TIMEZONE = "Asia/Shanghai"


class ApiError(BaseModel):
    """统一错误响应中的错误体。"""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiResponse(BaseModel):
    """统一接口响应外壳。"""

    success: bool
    data: Any = None
    error: ApiError | None = None
    request_id: str


class SignatureCreateRequest(BaseModel):
    """创建签名请求体。"""

    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=500000)
    is_default: bool = False


class SignatureUpdateRequest(BaseModel):
    """更新签名请求体。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=500000)


class ChangePasswordRequest(BaseModel):
    """修改密码请求体。"""

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ChangePasswordResponse(BaseModel):
    """修改密码响应体。"""

    password_updated: bool


class UserSettingsPreferences(BaseModel):
    """用户级设置偏好。"""

    page_size: int
    mark_read_on_open: bool
    language: str = DEFAULT_SETTINGS_LANGUAGE
    timezone: str = DEFAULT_SETTINGS_TIMEZONE
    reply_quote_position: str = Field(default=DEFAULT_SETTINGS_REPLY_QUOTE_POSITION)


class SystemSettingsPreferences(BaseModel):
    """系统级默认设置偏好。"""

    page_size: int = DEFAULT_SETTINGS_PAGE_SIZE
    mark_read_on_open: bool = DEFAULT_SETTINGS_MARK_READ_ON_OPEN
    language: str = DEFAULT_SETTINGS_LANGUAGE
    timezone: str = DEFAULT_SETTINGS_TIMEZONE
    reply_quote_position: str = Field(default=DEFAULT_SETTINGS_REPLY_QUOTE_POSITION)


class UserProfileSettings(BaseModel):
    """用户资料设置。"""

    display_name: str = ""
    profile_title: str = ""
    avatar_url: str = ""
    bio: str = ""


class ThemeSettings(BaseModel):
    """主题设置。"""

    mode: str = "light"


class SettingsPreferences(BaseModel):
    """完整设置视图。"""

    system: SystemSettingsPreferences = Field(default_factory=SystemSettingsPreferences)
    user: UserProfileSettings = Field(default_factory=UserProfileSettings)
    theme: ThemeSettings = Field(default_factory=ThemeSettings)


class SignatureResponse(BaseModel):
    """签名查询结果。"""

    id: str
    name: str
    content: str
    is_default: bool
    created_at: str
    updated_at: str


class ContactTagItem(BaseModel):
    """联系人标签项。"""

    name: str
    created_at: str


class ContactResponse(BaseModel):
    """联系人详情。"""

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
    """创建联系人请求体。"""

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
    """更新联系人请求体。"""

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
    """联系人列表响应。"""

    page: int
    page_size: int
    total: int
    items: list[ContactResponse]


class ContactSearchResponse(BaseModel):
    """联系人搜索响应。"""

    query: str
    contacts: list[ContactResponse | dict[str, str]]


class FolderCreateRequest(BaseModel):
    """创建文件夹请求体。"""

    name: str = Field(min_length=1, max_length=512)


class FolderRenameRequest(BaseModel):
    """重命名文件夹请求体。"""

    name: str = Field(min_length=1, max_length=512)
    new_name: str = Field(min_length=1, max_length=512)


class FolderDeleteRequest(BaseModel):
    """删除文件夹请求体。"""

    name: str = Field(min_length=1, max_length=512)


class FolderOperationResponse(BaseModel):
    """文件夹操作响应。"""

    folder: str
    new_name: str | None = None
    deleted: bool | None = None
