from datetime import date as date_value
from datetime import time as time_value
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class MeetingInfo(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    location_or_medium: Optional[str] = None
    attendees_external: List[str] = Field(default_factory=list)
    attendees_internal: List[str] = Field(default_factory=list)


class ActionItem(BaseModel):
    task: str
    assignee: str = "未確定"
    deadline: str = "未確定"


class NextMeetingInfo(BaseModel):
    detected: bool = False
    date_confirmed: bool = False
    title: Optional[str] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    @model_validator(mode="after")
    def keep_confirmation_consistent(self):
        """不完全・不正な日時を「確定」として後段へ渡さない。"""
        if not self.detected:
            self.date_confirmed = False
            self.date = None
            self.start_time = None
            self.end_time = None
            return self

        if not self.date_confirmed:
            self.date = None
            self.start_time = None
            self.end_time = None
            return self

        if not all((self.date, self.start_time, self.end_time)):
            self.date_confirmed = False
            self.date = None
            self.start_time = None
            self.end_time = None
            return self

        try:
            date_value.fromisoformat(self.date)
            start = time_value.fromisoformat(self.start_time)
            end = time_value.fromisoformat(self.end_time)
        except (TypeError, ValueError):
            self.date_confirmed = False
            self.date = None
            self.start_time = None
            self.end_time = None
            return self

        if start == end:
            self.date_confirmed = False
            self.date = None
            self.start_time = None
            self.end_time = None
        return self


class ExtractedMeetingData(BaseModel):
    meeting_info: MeetingInfo
    decisions: List[str] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)
    caution_warnings: List[str] = Field(default_factory=list)
    ambiguous_warnings: List[str] = Field(default_factory=list)
    next_meeting: NextMeetingInfo = Field(default_factory=NextMeetingInfo)
    summary: Optional[str] = None


class MeetingMinutes(BaseModel):
    title: str
    external_body: str
    source_data: ExtractedMeetingData


class FollowUpEmail(BaseModel):
    to: str = ""
    subject: str
    body: str
