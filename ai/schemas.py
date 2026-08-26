from typing import List, Optional

from pydantic import BaseModel, Field


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
