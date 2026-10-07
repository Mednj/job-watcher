import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Source = Literal[
    "linkedin",
    "hellowork",
    "apec",
    "glassdoor",
    "indeed",
    "jobteaser",
    "monster",
    "wttj",
    "francetravail",
]
SOURCES = (
    "linkedin",
    "hellowork",
    "apec",
    "glassdoor",
    "indeed",
    "jobteaser",
    "monster",
    "wttj",
    "francetravail",
)


class SearchInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sources: list[Source] = Field(default_factory=lambda: ["linkedin", "hellowork"], min_length=1)
    source: Source | None = Field(default=None, exclude=True)
    keywords: str = Field(min_length=1, max_length=200)
    location: str = Field(default="France", min_length=1, max_length=120)
    contract: Literal["any", "CDI", "CDD", "Alternance", "Stage", "Freelance"] = "any"
    experience: Literal["any", "entry", "experienced"] = "any"
    exclude_keywords: list[str] = Field(default_factory=list, max_length=30)
    interval_seconds: int = Field(default=60, ge=30, le=3600)
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_source(cls, value):
        if isinstance(value, dict) and "sources" not in value and value.get("source"):
            value = {**value, "sources": [value["source"]]}
        return value

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, value):
        return list(dict.fromkeys(value))

    def for_source(self, source: Source):
        return self.model_copy(update={"source": source})

    @field_validator("name", "keywords", "location")
    @classmethod
    def trim(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank")
        return value

    @field_validator("exclude_keywords")
    @classmethod
    def clean_exclusions(cls, values):
        result = list(dict.fromkeys(x.strip() for x in values if x.strip()))
        if any(len(x) > 80 for x in result):
            raise ValueError("Excluded keywords must be 80 characters or shorter")
        return result


def normalize(value: str) -> str:
    return " ".join(
        "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
        .casefold()
        .split()
    )


@dataclass
class Job:
    source: str
    source_id: str
    title: str
    company: str
    location: str
    url: str
    contract: str = "Not listed"
    published_at: str | None = None
    published_precision: str | None = None
    published_label: str = ""

    @property
    def key(self):
        return f"{self.source}:{self.source_id}"

    def matches(self, search: SearchInput) -> bool:
        text = normalize(f"{self.title} {self.company} {self.location}")
        if any(normalize(keyword) in text for keyword in search.exclude_keywords):
            return False
        title = normalize(self.title)
        keyword = normalize(search.keywords)
        if keyword == "it":
            terms = (
                "informatique",
                "it",
                "cloud",
                "devops",
                "dev ops",
                "data",
                "developpeur",
                "developpement informatique",
                "developpement logiciel",
                "developpement web",
                "developer",
                "software",
                "logiciel",
                "systeme",
                "systemes",
                "reseau",
                "reseaux",
                "network",
                "cybersecurite",
                "cybersecurity",
                "sre",
                "support technique",
                "technicien support",
                "qa",
                "testeur",
                "web",
                "full stack",
                "fullstack",
                "backend",
                "frontend",
                "database",
                "business intelligence",
            )
            matched = any(
                re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", title) for term in terms
            )
        else:
            # Require every keyword term in the title to remove irrelevant provider suggestions
            # (for example a tourism role in Saint-Cloud returned for the keyword cloud).
            terms = re.findall(r"\w+", keyword)
            matched = bool(terms) and all(
                re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", title) for term in terms
            )
        if self.source in ("hellowork", "wttj", "francetravail") and search.contract != "any":
            matched = matched and normalize(self.contract) == normalize(search.contract)
        return bool(matched)

    def to_dict(self):
        return asdict(self)


class ApplicationUpdate(BaseModel):
    applied: bool = Field(strict=True)


class RecruiterBan(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        value = value.strip()
        if not normalize(value) or normalize(value) in ("not listed", "unknown"):
            raise ValueError("Enter an identifiable company name")
        return value


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username")
    @classmethod
    def clean_username(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Username is required")
        return value


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class TelegramSettings(BaseModel):
    bot_token: str = Field(default="", max_length=256)
    chat_id: str = Field(default="", max_length=128)
