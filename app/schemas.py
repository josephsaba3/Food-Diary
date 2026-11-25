from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Category(StrEnum):
    breakfast = "breakfast"
    mid_morning_snack = "mid_morning_snack"
    lunch = "lunch"
    mid_afternoon_snack = "mid_afternoon_snack"
    dinner = "dinner"
    evening_snack = "evening_snack"


CATEGORIES = {"breakfast": "Breakfast", "mid_morning_snack": "Morning snack", "lunch": "Lunch",
              "mid_afternoon_snack": "Afternoon snack", "dinner": "Dinner", "evening_snack": "Evening snack"}
IMPORT_FROM = {"breakfast": "breakfast", "lunch": "dinner", "dinner": "dinner"}
MealTime = Annotated[str | None, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class MealInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    food: str = Field(min_length=1, max_length=5000)
    time: MealTime = None
    symptoms: str = Field(default="", max_length=5000)
    symptom_status: Literal["unrecorded", "none", "reported"] = "unrecorded"
    expected_version: int = Field(ge=0)

    @field_validator("time", mode="before")
    @classmethod
    def empty_time(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def symptoms_consistent(self):
        if self.symptoms and self.symptom_status == "none":
            raise ValueError("A no-symptoms entry cannot also contain symptoms.")
        if self.symptoms:
            self.symptom_status = "reported"
        elif self.symptom_status == "reported":
            raise ValueError("Describe the symptoms, or choose not recorded / no symptoms.")
        return self


class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(max_length=10000)
    expected_version: int = Field(ge=0)



class GarminKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=1, max_length=4000)


class GarminHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date | None = None
    end: date | None = None
    days: int = Field(default=180, ge=1, le=731)


class GarminAccountLogin(BaseModel):
    """Garmin Connect email and password: passed to Garmin's sign-in once, never stored or logged."""
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1000, repr=False)


class GarminAccountCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=12)


class GarminSync(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date | None = None
    end: date | None = None
    days: int = Field(default=1, ge=1, le=92)
