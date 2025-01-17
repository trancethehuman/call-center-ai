from datetime import datetime
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.claim import ClaimFieldModel, ClaimTypeEnum
from pydantic import BaseModel, EmailStr, Field, create_model, ConfigDict
from pydantic.fields import FieldInfo
from typing import Annotated, Any, Optional, Tuple, Union


class LanguageEntryModel(BaseModel):
    """
    Language entry, containing the standard short code, an human name and the Azure Text-to-Speech voice name.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts#supported-languages
    """

    pronunciations_en: list[str]
    short_code: str
    voice: str

    @property
    def human_name(self) -> str:
        return self.pronunciations_en[0]

    def __str__(self):  # Pretty print for logs
        return self.short_code


class LanguageModel(BaseModel):
    """
    Manage language for the workflow.
    """

    default_short_code: str = "fr-FR"
    # Voice list from Azure TTS
    # See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
    availables: list[LanguageEntryModel] = [
        LanguageEntryModel(
            pronunciations_en=["French", "FR", "France"],
            short_code="fr-FR",
            # Use voice optimized for conversational use
            # See: https://techcommunity.microsoft.com/t5/ai-azure-ai-services-blog/introducing-more-multilingual-ai-voices-optimized-for/ba-p/4012832
            voice="fr-FR-VivienneMultilingualNeural",
        ),
        LanguageEntryModel(
            short_code="en-US",
            pronunciations_en=["English", "EN", "United States"],
            # Use voice optimized for conversational use
            # See: https://techcommunity.microsoft.com/t5/ai-azure-ai-services-blog/introducing-more-multilingual-ai-voices-optimized-for/ba-p/4012832
            voice="en-US-AvaMultilingualNeural",
        ),
        LanguageEntryModel(
            short_code="es-ES",
            pronunciations_en=["Spanish", "ES", "Spain"],
            # Use voice optimized for conversational use
            # See: https://techcommunity.microsoft.com/t5/ai-azure-ai-services-blog/introducing-7-new-realistic-ai-voices-optimized-for/ba-p/3971966
            voice="es-ES-XimenaNeural",
        ),
        LanguageEntryModel(
            short_code="zh-CN",
            pronunciations_en=["Chinese", "ZH", "China"],
            # Use voice optimized for conversational use
            # See: https://techcommunity.microsoft.com/t5/ai-azure-ai-services-blog/introducing-more-multilingual-ai-voices-optimized-for/ba-p/4012832
            voice="zh-CN-XiaoxiaoMultilingualNeural",
        ),
    ]

    @property
    def default_lang(self) -> LanguageEntryModel:
        return next(
            (
                lang
                for lang in self.availables
                if self.default_short_code == lang.short_code
            ),
            self.availables[0],
        )


class WorkflowInitiateModel(BaseModel):
    agent_phone_number: PhoneNumber
    bot_company: str
    bot_name: str
    claim: list[ClaimFieldModel] = [
        ClaimFieldModel(name="extra_details", type=ClaimTypeEnum.TEXT),
        ClaimFieldModel(name="incident_datetime", type=ClaimTypeEnum.DATETIME),
        ClaimFieldModel(name="incident_description", type=ClaimTypeEnum.TEXT),
        ClaimFieldModel(name="incident_location", type=ClaimTypeEnum.TEXT),
        ClaimFieldModel(name="injuries", type=ClaimTypeEnum.TEXT),
        ClaimFieldModel(name="medical_records", type=ClaimTypeEnum.TEXT),
        ClaimFieldModel(name="parties", type=ClaimTypeEnum.TEXT),
        ClaimFieldModel(name="policy_number", type=ClaimTypeEnum.TEXT),
        ClaimFieldModel(name="pre_existing_damages", type=ClaimTypeEnum.TEXT),
        ClaimFieldModel(name="witnesses", type=ClaimTypeEnum.TEXT),
    ]  # Configured like in v4 for compatibility
    lang: LanguageModel = LanguageModel()  # Object is fully defined by default
    task: str = """
        Assistant will help the customer with their insurance claim. Assistant requires data from the customer to fill the claim. Claim data is located in the customer file. Assistant role is not over until all the relevant data is gathered.
    """

    def claim_model(self) -> type[BaseModel]:
        return _fields_to_pydantic(
            name="ClaimEntryModel",
            fields=[
                *self.claim,
                ClaimFieldModel(
                    description="Email of the customer",
                    name="policyholder_email",
                    type=ClaimTypeEnum.EMAIL,
                ),
                ClaimFieldModel(
                    description="First and last name of the customer",
                    name="policyholder_name",
                    type=ClaimTypeEnum.TEXT,
                ),
                ClaimFieldModel(
                    description="Phone number of the customer",
                    name="policyholder_phone",
                    type=ClaimTypeEnum.PHONE_NUMBER,
                ),
                ClaimFieldModel(
                    description="Relevant details gathered in the conversation than can be useful to solve the task",
                    name="extra_details",
                    type=ClaimTypeEnum.TEXT,
                ),
            ],
        )


class WorkflowModel(BaseModel):
    conversation_timeout_hour: int = 72  # 3 days
    initiate: WorkflowInitiateModel
    intelligence_hard_timeout_sec: int = 180  # 3 minutes
    intelligence_soft_timeout_sec: int = 30  # 30 seconds


def _fields_to_pydantic(name: str, fields: list[ClaimFieldModel]) -> type[BaseModel]:
    field_definitions = {field.name: _field_to_pydantic(field) for field in fields}
    return create_model(
        name,
        **field_definitions,  # type: ignore
        __config__=ConfigDict(
            extra="ignore",  # Avoid validation errors, just ignore data
        ),
    )


def _field_to_pydantic(
    field: ClaimFieldModel,
) -> Union[Annotated[Any, ...], Tuple[type, FieldInfo]]:
    type = _type_to_pydantic(field.type)
    return (
        Optional[type],
        Field(
            default=None,
            description=field.description,
        ),
    )


def _type_to_pydantic(
    data: ClaimTypeEnum,
) -> Union[type, Annotated[Any, ...]]:
    if data == ClaimTypeEnum.DATETIME:
        return datetime
    elif data == ClaimTypeEnum.EMAIL:
        return EmailStr
    elif data == ClaimTypeEnum.PHONE_NUMBER:
        return PhoneNumber
    elif data == ClaimTypeEnum.TEXT:
        return str
    else:
        raise ValueError(f"Unsupported data: {data}")
